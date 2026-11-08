"""Module 07 OCR: read a scanned order sheet into reviewable lines.

Rules (docs/modules/07-ocr.md): OCR never creates an invoice or a stock movement; it only proposes
lines that a person reviews. Anything unclear (unreadable, low confidence, unknown product) must be
corrected or removed by the reviewer. What the engine read is kept unchanged as evidence.

Providers run server-side only (credentials never reach the browser):
- ``fake``: reads text drawn into synthetic PDFs (our own uncompressed content streams). Text in
  font /F3 counts as handwriting; a trailing ``?`` marks a low-confidence read. Dev/test only.
- ``tesseract``: local Tesseract 5 (scanned images and image-only PDFs; PDF pages rasterized at
  300 dpi by poppler ``pdftoppm``; orientation detected with OSD). Confidence is the engine's.
- ``document_ai``: Google Document AI REST (unverified until the office pilot).
- ``disabled``: no engine; the reviewer types the lines (manual path always works).
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from . import audit, outbox
from . import inventory as inv
from .config import get_settings
from .errors import ApiError
from .invoice_pdf import MARGIN, PAGE_H, PAGE_W, _assemble, _clip, _Page
from .models import (
    Customer,
    OcrExtraction,
    OcrField,
    OutboxEvent,
    Product,
    ProductAlias,
    ScanJob,
    SourceDocument,
    Warehouse,
)

EXTRACT_EVENT = "ocr.extract"
TEMPLATE = "PT-OS-1"
UNITS = ("LB", "KG", "BOX", "PIECE")
LIVE = ("QUEUED", "NEEDS_REVIEW", "FAILED")
ROW_TOLERANCE = Decimal("0.008")  # same printed row if centres are within ~6pt on Letter
QTY_RE = re.compile(r"^\d{1,6}(\.\d{1,4})?$")
C3 = Decimal("0.001")


def now() -> datetime:
    return datetime.now(UTC)


def norm(s: str) -> str:
    return " ".join(s.casefold().split())


# ------------------------------------------------------------------ provider interface


@dataclass(frozen=True)
class Token:
    text: str
    page: int
    x0: Decimal  # normalized 0..1, origin top-left
    y0: Decimal
    x1: Decimal
    y1: Decimal
    confidence: Decimal
    handwritten: bool

    @property
    def yc(self) -> Decimal:
        return (self.y0 + self.y1) / 2


@dataclass(frozen=True)
class OcrResult:
    pages: int
    tokens: list[Token]


class ProviderError(Exception):
    def __init__(self, code: str, detail: str, transient: bool) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail, self.transient = code, detail, transient


Provider = Callable[[bytes, str], OcrResult]

_PDF_TEXT = re.compile(
    rb"BT /(F\d+) ([\d.]+) Tf (-?[\d.]+) (-?[\d.]+) Td \(((?:\\.|[^\\)])*)\) Tj ET"
)


def _d(v: float | str | Decimal) -> Decimal:
    return Decimal(str(v))


def fake_provider(content: bytes, mime: str) -> OcrResult:
    """Deterministic stand-in for an OCR engine. Images carry no text layer -> no tokens."""
    if mime != "application/pdf":
        return OcrResult(1, [])
    streams = re.findall(rb"stream\n(.*?)\nendstream", content, re.S)
    tokens: list[Token] = []
    page = 0
    for st in streams:
        hits = _PDF_TEXT.findall(st)
        if not hits:
            continue
        page += 1
        for font, size_b, x_b, y_b, raw in hits:
            text = re.sub(rb"\\(.)", rb"\1", raw).decode("cp1252", errors="replace").strip()
            if not text:
                continue
            size, x, y = _d(size_b.decode()), _d(x_b.decode()), _d(y_b.decode())
            hand = font == b"F3"
            conf = Decimal("0.995")
            if hand:
                conf = Decimal("0.980")
                if text.endswith("?"):
                    text, conf = text[:-1].strip() or "?", Decimal("0.620")
            w = size * Decimal("0.55") * len(text)
            tokens.append(
                Token(
                    text=text,
                    page=page,
                    x0=(x / PAGE_W).quantize(C3),
                    y0=((PAGE_H - y - size) / PAGE_H).quantize(C3),
                    x1=((x + w) / PAGE_W).quantize(C3),
                    y1=((PAGE_H - y) / PAGE_H).quantize(C3),
                    confidence=conf,
                    handwritten=hand,
                )
            )
    return OcrResult(max(page, 1), tokens)


def _google_token(creds_file: str, timeout: int) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    with open(creds_file, encoding="utf-8") as fh:
        sa = json.load(fh)

    def b64(b: bytes) -> str:
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    t = int(time.time())
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = b64(
        json.dumps(
            {
                "iss": sa["client_email"],
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "aud": sa["token_uri"],
                "iat": t,
                "exp": t + 600,
            }
        ).encode()
    )
    key = serialization.load_pem_private_key(sa["private_key"].encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ProviderError("OCR_NOT_CONFIGURED", "service account key is not RSA", False)
    sig = key.sign(f"{head}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    form = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": f"{head}.{claims}.{b64(sig)}",
        }
    ).encode()
    if not str(sa.get("token_uri", "")).startswith("https://"):
        raise ProviderError("OCR_NOT_CONFIGURED", "token_uri must be https", False)
    req = urllib.request.Request(sa["token_uri"], data=form, method="POST")  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URI
        return str(json.loads(r.read())["access_token"])


def document_ai_provider(content: bytes, mime: str) -> OcrResult:
    """Google Document AI (Enterprise OCR) over REST. UNVERIFIED until the office pilot."""
    s = get_settings()
    if not s.ocr_docai_endpoint.startswith("https://") or not s.ocr_docai_credentials_file:
        raise ProviderError("OCR_NOT_CONFIGURED", "Document AI is not configured", False)
    try:
        token = _google_token(s.ocr_docai_credentials_file, s.ocr_timeout_seconds)
        body = json.dumps(
            {"rawDocument": {"content": base64.b64encode(content).decode(), "mimeType": mime}}
        ).encode()
        req = urllib.request.Request(  # noqa: S310 - https checked above
            s.ocr_docai_endpoint,
            data=body,
            method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=s.ocr_timeout_seconds) as r:  # noqa: S310
            doc = json.loads(r.read(), parse_float=Decimal)["document"]
    except urllib.error.HTTPError as e:
        raise ProviderError(
            "OCR_UNAVAILABLE" if e.code >= 500 or e.code == 429 else "OCR_REJECTED",
            f"Document AI HTTP {e.code}",
            e.code >= 500 or e.code == 429,
        ) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise ProviderError("OCR_UNAVAILABLE", f"Document AI unreachable: {e}", True) from None
    except (OSError, KeyError, ValueError) as e:
        raise ProviderError("OCR_NOT_CONFIGURED", f"Document AI setup: {e}", False) from None
    text: str = doc.get("text", "")
    tokens: list[Token] = []
    for pi, page in enumerate(doc.get("pages", []), start=1):
        for t in page.get("tokens", []):
            lay = t.get("layout", {})
            segs = lay.get("textAnchor", {}).get("textSegments", [])
            s_txt = "".join(text[int(g.get("startIndex", 0)) : int(g["endIndex"])] for g in segs)
            verts = lay.get("boundingPoly", {}).get("normalizedVertices", [])
            if not s_txt.strip() or not verts:
                continue
            xs = [_d(v.get("x", 0)) for v in verts]
            ys = [_d(v.get("y", 0)) for v in verts]
            tokens.append(
                Token(
                    text=s_txt.strip(),
                    page=pi,
                    x0=min(xs).quantize(C3),
                    y0=min(ys).quantize(C3),
                    x1=max(xs).quantize(C3),
                    y1=max(ys).quantize(C3),
                    confidence=_d(lay.get("confidence", 0)).quantize(C3),
                    handwritten=bool(t.get("styleInfo", {}).get("handwritten", False)),
                )
            )
    return OcrResult(max(len(doc.get("pages", [])), 1), tokens)


_IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/tiff": ".tif"}


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False, shell=False)  # noqa: S603
    except FileNotFoundError:
        raise ProviderError("OCR_ENGINE_MISSING", f"{args[0]} is not installed", False) from None
    except subprocess.TimeoutExpired:
        raise ProviderError("OCR_TIMEOUT", "OCR took too long", True) from None


def _rotation(img: str, timeout: int) -> int:
    """Degrees to rotate (OSD). 0 when the page has too little text to tell."""
    r = _run([get_settings().ocr_tesseract_cmd, img, "-", "--psm", "0"], timeout)
    m = re.search(rb"Rotate: (\d+)", r.stdout)
    return int(m.group(1)) if m and r.returncode == 0 else 0


def _prepare(img: Path, rot: int) -> None:
    """Grayscale, undo the detected rotation, stretch contrast. Rewrites the file as PNG."""
    from PIL import Image, ImageOps

    try:
        with Image.open(img) as im:
            g = ImageOps.exif_transpose(im).convert("L")
    except OSError:
        raise ProviderError("UNREADABLE", "The image could not be opened", False) from None
    if rot:
        g = g.rotate(-rot, expand=True, fillcolor=255)
    ImageOps.autocontrast(g, cutoff=1).save(img, format="PNG")


def tesseract_provider(content: bytes, mime: str) -> OcrResult:
    st = get_settings()
    t = st.ocr_timeout_seconds
    with tempfile.TemporaryDirectory(prefix="ptocr-") as tmp:
        if mime == "application/pdf":
            src = Path(tmp) / "in.pdf"
            src.write_bytes(content)
            r = _run(["pdftoppm", "-r", "300", "-gray", "-png", str(src), str(Path(tmp) / "p")], t)
            if r.returncode != 0:
                raise ProviderError("UNREADABLE", "The PDF could not be opened", False)
            images = sorted(Path(tmp).glob("p-*.png"))
        elif mime in _IMG_EXT:
            img = Path(tmp) / f"in{_IMG_EXT[mime]}"
            img.write_bytes(content)
            images = [img]
        else:
            raise ProviderError("UNSUPPORTED", f"Can't read {mime}", False)
        if not images:
            raise ProviderError("UNREADABLE", "No pages found", False)
        tokens: list[Token] = []
        for n, img in enumerate(images, start=1):
            rot = _rotation(str(img), t)
            _prepare(img, rot)
            args = [st.ocr_tesseract_cmd, str(img), "-", "--psm", "3", "-l", "eng", "tsv"]
            r = _run(args, t)
            if r.returncode != 0:
                raise ProviderError("OCR_FAILED", r.stderr.decode(errors="replace")[:200], False)
            w = h = Decimal(1)
            for line in r.stdout.decode("utf-8", "replace").splitlines()[1:]:
                f = line.split("\t")
                if len(f) < 12:
                    continue
                left, top, width, height = (_d(x) for x in f[6:10])
                if f[0] == "1":
                    w, h = max(width, Decimal(1)), max(height, Decimal(1))
                    continue
                text, conf = f[11].strip(), _d(f[10])
                if f[0] != "5" or not text or conf < 0:
                    continue
                tokens.append(
                    Token(
                        text=text, page=n,
                        x0=(left / w).quantize(C3), y0=(top / h).quantize(C3),
                        x1=((left + width) / w).quantize(C3), y1=((top + height) / h).quantize(C3),
                        confidence=(conf / 100).quantize(C3), handwritten=False,
                    )
                )  # fmt: skip
        return OcrResult(len(images), tokens)


def provider() -> Provider | None:
    name = get_settings().ocr_provider
    if name == "fake":
        if get_settings().is_production_like:
            raise ProviderError("OCR_NOT_CONFIGURED", "fake OCR is not allowed here", False)
        return fake_provider
    if name == "tesseract":
        return tesseract_provider
    if name == "document_ai":
        return document_ai_provider
    return None


# ------------------------------------------------------------------ mapping


@dataclass
class Proposal:
    kind: str
    label: str
    raw_text: str
    product_id: uuid.UUID | None
    unit_code: str | None
    qty: Decimal | None
    confidence: Decimal | None
    status: str
    bbox: dict[str, Any] | None


def rows_of(tokens: list[Token]) -> list[list[Token]]:
    rows: list[list[Token]] = []
    for t in sorted(tokens, key=lambda k: (k.page, k.yc, k.x0)):
        last = rows[-1] if rows else None
        if last and last[0].page == t.page and abs(last[0].yc - t.yc) <= ROW_TOLERANCE:
            last.append(t)
        else:
            rows.append([t])
    return [sorted(r, key=lambda k: k.x0) for r in rows]


def _bbox(row: list[Token]) -> dict[str, Any]:
    return {
        "page": row[0].page,
        "x0": str(min(t.x0 for t in row)),
        "y0": str(min(t.y0 for t in row)),
        "x1": str(max(t.x1 for t in row)),
        "y1": str(max(t.y1 for t in row)),
    }


def parse_qty(text: str) -> Decimal | None:
    t = text.replace(",", "").replace(" ", "")
    if not QTY_RE.match(t):
        return None
    try:
        q = Decimal(t)
    except InvalidOperation:
        return None
    return q if q > 0 else None


class Catalog:
    def __init__(self, db: Session, company_id: uuid.UUID) -> None:
        self.db = db
        prods = db.scalars(
            select(Product).where(Product.company_id == company_id, Product.active.is_(True))
        ).all()
        self.by_id = {p.id: p for p in prods}
        self.by_sku = {p.sku.casefold(): p for p in prods}
        self.by_name = {norm(p.name): p for p in prods}
        self.by_alias: dict[str, Product] = {}
        for a in db.scalars(
            select(ProductAlias).where(ProductAlias.company_id == company_id)
        ).all():
            if a.product_id in self.by_id:
                self.by_alias.setdefault(a.alias_norm, self.by_id[a.product_id])

    def match(self, text: str) -> Product | None:
        n = norm(text)
        return self.by_sku.get(n) or self.by_alias.get(n) or self.by_name.get(n)

    def unit_ok(self, prod: Product, unit: str) -> bool:
        return unit in inv.stock_units(self.db, prod)


def _status(cat: Catalog, prod: Product | None, unit: str | None, qty: Decimal | None,
            conf: Decimal | None, threshold: Decimal) -> str:  # fmt: skip
    if qty is None:
        return "UNREADABLE"
    if prod is None or unit is None or not cat.unit_ok(prod, unit):
        return "UNMAPPED"
    try:
        inv.check_qty(unit, qty)
    except ApiError:
        return "LOW_CONFIDENCE"
    if conf is None or conf < threshold:
        return "LOW_CONFIDENCE"
    return "OK"


def _free_text(cat: Catalog, row: list[Token], threshold: Decimal) -> Proposal | None:
    """A written line like 'Ackee 3 BOX' or a vendor/alias line: product words + qty + unit."""
    words = [w for t in row for w in t.text.split()]
    qty_i = next((i for i in range(len(words) - 1, -1, -1) if parse_qty(words[i])), None)
    hand = [t for t in row if t.handwritten]
    if qty_i is None and not hand:
        return None
    unit = next((w.upper() for w in words if w.upper() in UNITS), None)
    rest = [w for i, w in enumerate(words) if i != qty_i and w.upper() not in UNITS]
    prod = cat.match(" ".join(rest)) if rest else None
    qty = parse_qty(words[qty_i]) if qty_i is not None else None
    if prod and unit is None:
        unit = prod.stock_unit
    conf = min(t.confidence for t in row)
    return Proposal(
        kind="EXTRA",
        label=f"{prod.sku} {prod.name}" if prod else " ".join(rest)[:200],
        raw_text=" ".join(words)[:500],
        product_id=prod.id if prod else None,
        unit_code=unit,
        qty=qty,
        confidence=conf,
        status=_status(cat, prod, unit, qty, conf, threshold),
        bbox=_bbox(row),
    )


@dataclass
class MappedSheet:
    template: str | None
    customer_id: uuid.UUID | None
    proposals: list[Proposal]
    warnings: list[str]
    text_blocks: list[dict[str, Any]]


def _code_key(s: str) -> str:
    return s.casefold().translate(str.maketrans("oils", "0115"))


def _one_edit(a: str, b: str) -> bool:
    """True when a and b differ by one inserted, deleted or changed character."""
    if abs(len(a) - len(b)) > 1 or a == b:
        return False
    i = 0
    while i < min(len(a), len(b)) and a[i] == b[i]:
        i += 1
    return a[i + 1 :] == b[i + 1 :] or a[i:] == b[i + 1 :] or a[i + 1 :] == b[i:]


def map_result(db: Session, company_id: uuid.UUID, job_type: str, res: OcrResult) -> MappedSheet:
    threshold = Decimal(get_settings().ocr_confidence_threshold)
    cat = Catalog(db, company_id)
    rows = rows_of(res.tokens)
    texts = [" ".join(t.text for t in r) for r in rows]
    template = TEMPLATE if any(t.text == TEMPLATE for t in res.tokens) else None
    warnings: list[str] = []
    if not res.tokens:
        warnings.append("NO_TEXT")
    elif job_type == "SALES_ORDER" and template is None:
        warnings.append("NO_TEMPLATE")

    customer_id: uuid.UUID | None = None
    for r in rows:
        words = [t.text for t in r]
        if "Ref:" in words and words.index("Ref:") + 1 < len(words):
            ref = words[words.index("Ref:") + 1]
            cust = db.scalar(
                select(Customer).where(
                    Customer.company_id == company_id,
                    func.lower(Customer.external_ref) == ref.casefold(),
                )
            )
            if cust is None:
                warnings.append("CUSTOMER_NOT_FOUND")
            else:
                customer_id = cust.id
            break

    # Pixel OCR can't tell handwriting from print: on our sheet, what sits in the Quantity
    # column, and every line under "Other items", is what the customer filled in.
    pixel = template is not None and not any(t.handwritten for t in res.tokens)
    fuzzy = {_code_key(k): v for k, v in cat.by_sku.items()}
    qty_x = next((t.x0 for t in res.tokens if t.text == "Quantity"), None)
    other_y = next(
        (
            r[0].yc
            for r in rows
            if r and r[0].text == "Other" and len(r) > 1 and r[1].text == "items"
        ),
        None,
    )

    def filled(r: list[Token]) -> list[Token]:
        if not pixel:
            return [t for t in r if t.handwritten]
        if other_y is not None and r[0].yc > other_y + ROW_TOLERANCE:
            return list(r)
        return [t for t in r if qty_x is not None and t.x0 >= qty_x - Decimal("0.02")]

    out: list[Proposal] = []
    for r in rows:
        hand = filled(r)
        printed = [t for t in r if t not in hand]
        head = cat.by_sku.get(printed[0].text.casefold()) if template and printed else None
        guessed = False
        if head is None and template and printed:
            # common OCR slips in codes: O/0, I/l/1, S/5 — matched, but sent for review
            k = _code_key(printed[0].text)
            head = fuzzy.get(k)
            if head is None:
                near = [v for c, v in fuzzy.items() if _one_edit(c, k)]
                head = near[0] if len(near) == 1 else None
            guessed = head is not None
        if head is not None:
            if not hand:
                continue  # blank quantity box = not ordered
            unit = next((t.text for t in printed if t.text in UNITS), head.stock_unit)
            raw = " ".join(t.text for t in hand)
            qty = parse_qty(raw)
            conf = min(t.confidence for t in hand)
            if guessed:
                conf = min(conf, threshold - C3)
            out.append(
                Proposal(
                    kind="LINE",
                    label=f"{head.sku} {head.name}",
                    raw_text=raw[:500],
                    product_id=head.id,
                    unit_code=unit,
                    qty=qty,
                    confidence=conf,
                    status=_status(cat, head, unit, qty, conf, threshold),
                    bbox=_bbox(r),
                )
            )
        elif hand or not template:
            # write-in line on the sheet, or any line of a non-template document
            p = _free_text(cat, hand if template else r, threshold)
            if p is not None and (hand or p.product_id is not None):
                out.append(p)
    if res.tokens and not out:
        warnings.append("NO_LINES")
    blocks = [{"page": r[0].page, "text": t[:300]} for r, t in zip(rows, texts, strict=True)]
    return MappedSheet(template, customer_id, out[:200], warnings, blocks[:400])


# ------------------------------------------------------------------ lifecycle


def queue(db: Session, job: ScanJob, actor_id: uuid.UUID | None) -> OcrExtraction:
    """Start a (new) OCR run for a received scan. A previous unfinished run is superseded."""
    if job.status != "UPLOADED" or job.document_id is None:
        raise ApiError(409, "SCAN_NOT_RECEIVED", "This scan has no document yet")
    live = db.scalar(
        select(OcrExtraction)
        .where(OcrExtraction.scan_job_id == job.id, OcrExtraction.status != "SUPERSEDED")
        .with_for_update()
    )
    if live is not None:
        if live.status not in LIVE:
            raise ApiError(
                409,
                "EXTRACTION_FINAL",
                f"This scan was already {live.status.lower()}",
                current_status=live.status,
            )
        live.status = "SUPERSEDED"
        live.version += 1
        db.flush()
    run = (db.scalar(select(func.max(OcrExtraction.run_no)).where(
        OcrExtraction.scan_job_id == job.id)) or 0) + 1  # fmt: skip
    engine = get_settings().ocr_provider
    ex = OcrExtraction(
        company_id=job.company_id,
        warehouse_id=job.warehouse_id,
        scan_job_id=job.id,
        document_id=job.document_id,
        run_no=run,
        status="QUEUED" if engine != "disabled" else "NEEDS_REVIEW",
        provider=engine if engine != "disabled" else "manual",
        warnings=[] if engine != "disabled" else ["OCR_DISABLED"],
        requested_by=actor_id,
        completed_at=None if engine != "disabled" else now(),
    )
    db.add(ex)
    db.flush()
    if ex.status == "QUEUED":
        outbox.enqueue(
            db,
            EXTRACT_EVENT,
            {"extraction_id": str(ex.id)},
            company_id=job.company_id,
            aggregate_id=str(ex.id),
        )
    return ex


def auto_queue(db: Session, job: ScanJob, actor_id: uuid.UUID | None) -> None:
    """Sales-order scans are read automatically when they arrive (if an engine is configured)."""
    if job.job_type == "SALES_ORDER" and get_settings().ocr_provider != "disabled":
        queue(db, job, actor_id)


def run_extraction(db: Session, ex: OcrExtraction, attempt: int, final_attempt: int) -> None:
    """Call the engine, then write fields atomically. Transient failures raise (outbox retries)."""
    if ex.status != "QUEUED":
        return  # superseded or already done: nothing to do (idempotent)
    doc = db.get(SourceDocument, ex.document_id)
    job = db.get(ScanJob, ex.scan_job_id)
    assert doc is not None and job is not None
    try:
        prov = provider()
        if prov is None:
            raise ProviderError("OCR_DISABLED", "No OCR engine configured", False)
        res = prov(doc.content, doc.mime_type)
    except ProviderError as e:
        if e.transient and attempt < final_attempt:
            raise
        with db.begin_nested():
            ex.status, ex.error_code, ex.error_detail = "FAILED", e.code, e.detail[:500]
            ex.completed_at = now()
            ex.version += 1
            audit.record(
                db,
                action="ocr.extract",
                success=False,
                company_id=ex.company_id,
                target_type="ocr_extraction",
                target_id=str(ex.id),
                warehouse_id=ex.warehouse_id,
                detail={"code": e.code, "attempts": attempt},
            )
        return
    m = map_result(db, ex.company_id, job.job_type, res)
    with db.begin_nested():
        for i, p in enumerate(m.proposals, start=1):
            db.add(
                OcrField(
                    extraction_id=ex.id,
                    seq=i,
                    kind=p.kind,
                    label=p.label,
                    raw_text=p.raw_text,
                    read_product_id=p.product_id,
                    read_unit_code=p.unit_code,
                    read_qty=p.qty,
                    confidence=p.confidence,
                    status=p.status,
                    bbox=p.bbox,
                )
            )
        ex.status = "NEEDS_REVIEW"
        ex.template, ex.customer_id = m.template, m.customer_id
        ex.warnings, ex.text_blocks, ex.page_count = m.warnings, m.text_blocks, res.pages
        ex.completed_at = now()
        ex.version += 1
        audit.record(
            db,
            action="ocr.extract",
            success=True,
            company_id=ex.company_id,
            target_type="ocr_extraction",
            target_id=str(ex.id),
            warehouse_id=ex.warehouse_id,
            detail={"fields": len(m.proposals), "template": m.template, "warnings": m.warnings},
        )


def handle_extract(ev: OutboxEvent) -> None:
    db = object_session(ev)
    assert db is not None
    ex = db.get(OcrExtraction, uuid.UUID(ev.payload["extraction_id"]), with_for_update=True)
    if ex is None:
        raise LookupError("extraction not found")
    run_extraction(db, ex, ev.attempts, outbox.MAX_ATTEMPTS)


# ------------------------------------------------------------------ printable order sheet


def order_sheet_pdf(
    db: Session,
    customer: Customer,
    wh: Warehouse,
    company_name: str,
    fills: dict[str, str] | None = None,
    write_ins: list[str] | None = None,
) -> bytes:
    """Printable PT-OS-1 sheet: one row per active product, a box for the handwritten quantity.
    `fills`/`write_ins` draw synthetic handwriting - used only to build test documents."""
    prods = db.scalars(
        select(Product)
        .where(Product.company_id == customer.company_id, Product.active.is_(True))
        .order_by(Product.sku)
    ).all()
    fills = fills or {}
    per_page = 30
    chunks = [prods[i : i + per_page] for i in range(0, len(prods), per_page)] or [[]]
    pages: list[_Page] = []
    ref = customer.external_ref or str(customer.id)
    for n, chunk in enumerate(chunks, start=1):
        pg = _Page()
        top = PAGE_H - MARGIN
        pg.text(MARGIN, top, company_name, 14, bold=True)
        pg.text(MARGIN, top - 18, "ORDER SHEET", 11, bold=True)
        pg.text(PAGE_W - MARGIN - 60, top, TEMPLATE, 10, bold=True)
        pg.text(MARGIN, top - 40, f"Customer: {_clip(customer.name, 50)}", 10)
        pg.text(MARGIN, top - 54, "Ref:", 10)
        pg.text(MARGIN + 26, top - 54, ref, 10)
        pg.text(330, top - 40, f"Location: {wh.code}", 10)
        pg.text(330, top - 54, "Date: ______________", 10)
        pg.text(PAGE_W - MARGIN - 60, top - 18, f"Page {n}/{len(chunks)}", 8)
        y = top - 84
        pg.text(MARGIN, y, "SKU", 8, bold=True)
        pg.text(MARGIN + 90, y, "Product", 8, bold=True)
        pg.text(400, y, "Unit", 8, bold=True)
        pg.text(460, y, "Quantity", 8, bold=True)
        pg.line(MARGIN, y - 4, PAGE_W - MARGIN, y - 4)
        for p in chunk:
            y -= 20
            pg.text(MARGIN, y, p.sku, 9)
            pg.text(MARGIN + 90, y, _clip(p.name, 48), 9)
            pg.text(400, y, p.stock_unit, 9)
            pg.line(455, y - 4, PAGE_W - MARGIN, y - 4)
            if p.sku in fills:
                pg.hand(470, y - 1, fills[p.sku], 9)
        if n == len(chunks):
            y -= 30
            pg.text(MARGIN, y, "Other items (write product and quantity):", 9, bold=True)
            for i in range(3):
                y -= 22
                pg.line(MARGIN, y - 4, PAGE_W - MARGIN, y - 4)
                if write_ins and i < len(write_ins):
                    pg.hand(MARGIN + 4, y - 1, write_ins[i], 9)
        pages.append(pg)
    return _assemble(pages, f"Order sheet {ref}")
