# ruff: noqa: F811, N806, E731, S603, S607 - fixtures re-used by name; short test helpers
"""Gemini as an OCR engine (OCR_PROVIDER=gemini).

Every test here uses SYNTHETIC sheets and a STUBBED Google answer (`gemini_client.transport` is
replaced), so they prove the server's own logic: the request it builds, the limits it keeps, the
rules its answer is held to, the fallback, and that a reading only ever becomes a draft. They
say nothing about how well Gemini reads: the one real call is `test_live_gemini_reads_a_
synthetic_sheet`, which runs only with GEMINI_LIVE_TEST=1 and a key in the environment.
"""

import json
import os
import shutil
import subprocess
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from pro_trading import gemini_client, gemini_ocr, paper_sheet
from pro_trading import gemini_rules as rules
from pro_trading.config import get_settings
from pro_trading.db import get_sessionmaker
from pro_trading.models import OcrEngineCall

from .conftest import idem
from .test_ocr import P, code, counts, drain, only, owner, resolve, user  # noqa: F401
from .test_paper_sheet import FILLS, blocks, catalog, post_file  # noqa: F401
from .test_supplier_ocr import invoice_pdf, scan, setup, vendor  # noqa: F401

D = Decimal
needs_tools = pytest.mark.skipif(
    not (shutil.which("tesseract") and shutil.which("pdftoppm")), reason="no tesseract/pdftoppm"
)
KEY = "test-key-not-a-real-one"


def row(item, qty=None, **kw):
    base = {
        "t": 1, "item": item, "w": qty is not None, "hand": False, "qty": qty, "old": None,
        "pkg": None, "wt": None, "size": None, "notes": None, "no": False, "chk": False,
        "sup": None, "unav": None, "price": None, "amt": None, "unsure": False, "img": 0,
        "box": [],
    }  # fmt: skip
    return {**base, **kw}


def page(rows, **kw):
    base = {
        "doc_type": "CUSTOMER_ORDER", "issuer": "Tamisha Trading Inc", "customer": None,
        "cust_unsure": False, "order_ref": None, "doc_no": None, "doc_date": None, "awb": None,
        "currency": None, "total": None, "layout": "3 tables ITEM|Q|PKG|WEIGHT|NOTES",
        "totals": [], "rows": rows, "problems": [],
    }  # fmt: skip
    return {**base, **kw}


def envelope(obj, tokens=(1200, 300)):
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return json.dumps({  # as generateContent answers
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": tokens[0], "candidatesTokenCount": tokens[1],
                          "totalTokenCount": sum(tokens)},
    }).encode()  # fmt: skip


class Google:
    """Stands in for Google: answers from a queue (the last answer repeats) and keeps what it
    was sent."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.sent: list[tuple[dict, dict]] = []
        self.urls: list[str] = []

    def __call__(self, url, headers, body, timeout):
        self.urls.append(url)
        self.sent.append((headers, json.loads(body)))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        if isinstance(a, tuple):
            return a[0], {}, a[1] if isinstance(a[1], bytes) else json.dumps(a[1]).encode()
        return 200, {}, envelope(a)


@pytest.fixture(autouse=True)
def no_network(monkeypatch, request):
    """Nothing in this file may reach Google except the one test that says so (the developer's
    own .env can hold a real key)."""
    if request.node.name.startswith("test_live_"):
        return

    def refuse(*a, **kw):
        raise AssertionError("a test tried to call Google")

    monkeypatch.setattr(gemini_client, "transport", refuse)


@pytest.fixture
def gemini(monkeypatch):
    """Gemini switched on, with consent, a fake key and no waiting; `use(...)` sets the answers."""
    for k, v in {
        "OCR_PROVIDER": "gemini", "GEMINI_API_KEY": KEY, "GEMINI_DATA_CONSENT": "true",
        "GEMINI_REQUESTS_PER_MINUTE": "600", "GEMINI_DAILY_REQUEST_CAP": "20",
        "GEMINI_MAX_RETRIES": "1", "GEMINI_BREAKER_FAILURES": "3", "GEMINI_RECHECK_RESERVE": "5",
        "GEMINI_MODEL": "gemini-3.8-flash", "GEMINI_TIMEOUT_SECONDS": "90",
        "GEMINI_API": "generate_content",
    }.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    monkeypatch.setattr(gemini_client.time, "sleep", lambda s: None)
    with get_sessionmaker()() as db, db.begin():
        db.execute(delete(OcrEngineCall))

    def use(*answers):
        g = Google(*answers)
        monkeypatch.setattr(gemini_client, "transport", g)
        return g

    yield use
    monkeypatch.setenv("OCR_PROVIDER", "fake")
    for k in ("GEMINI_API_KEY", "GEMINI_DATA_CONSENT"):
        monkeypatch.delenv(k)
    get_settings.cache_clear()


def setenv(monkeypatch, **kw):
    for k, v in kw.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def calls():
    with get_sessionmaker()() as db:
        return [
            (c.stage, c.outcome)
            for c in db.scalars(select(OcrEngineCall).order_by(OcrEngineCall.id))
        ]


def by_sku(ex):
    return {f["read_sku"] or f["label"]: f for f in ex["fields"]}


# ------------------------------------------------------------------ rules (no database)


def test_quantities_stay_what_was_written():
    q = lambda s: rules.hand_qty(s)[0]  # noqa: E731
    assert [q("1"), q("1/2"), q("1.5"), q("5"), q("15")] == [D(1), D("0.5"), D("1.5"), D(5), D(15)]
    assert q("1 1/2") == D("1.5") and q("½") == D("0.5") and q("1½") == D("1.5")
    # a fraction or a decimal is never waved through; a whole number is
    assert rules.hand_qty("1/2")[2] == ["FRACTION"] and rules.hand_qty("1.5")[2] == ["FRACTION"]
    assert rules.hand_qty("15")[2] == []
    assert rules.hand_qty("2 cs") == (D(2), "cs", [])
    for junk in ("NO", "x", "✓", "-", "1/0", "0", "", "12a4"):
        assert q(junk) is None, junk
    assert rules.hand_qty(None) == (None, None, [])


def test_answer_must_match_the_schema():
    good = page([row("Okra", "12")])
    assert rules.validate_page(good).rows[0].qty == "12"
    for bad in (
        {**good, "extra": 1},
        {**good, "doc_type": "PURCHASE"},
        {**good, "rows": [{**row("Okra", "12"), "qty": 12}]},  # numbers must come as text
        {**good, "rows": [{**row("Okra", "12"), "confidence": 0.99}]},
        {k: v for k, v in good.items() if k != "rows"},
    ):
        with pytest.raises(ValueError):
            rules.validate_page(bad)
    # a senseless highlight box or an over-long text is repaired, not a reason to lose the page
    odd = rules.validate_page({**good, "rows": [{**row("x" * 400, "1"), "box": [0, 0, 5000, 10]}]})
    assert odd.rows[0].box == [] and len(odd.rows[0].item) == 300
    # every property the schema promises is one the validator knows, and the other way round
    assert set(rules.PAGE_SCHEMA["properties"]) == set(rules.PageOut.model_fields)
    assert set(rules.PAGE_SCHEMA["properties"]["rows"]["items"]["properties"]) == set(
        rules.RowOut.model_fields
    )


def test_untouched_printed_rows_and_totals_are_not_lines():
    R = rules.RowOut.model_validate
    assert rules.is_untouched(R(row("Okra")))
    assert not rules.is_untouched(R(row("Okra", "3")))
    assert not rules.is_untouched(R(row("Okra", no=True, w=True)))
    assert rules.is_total_row(R(row("TOTAL", "67"))) and rules.is_total_row(
        R(row("Sub-total", "5"))
    )
    assert not rules.is_total_row(R(row("Tomato Beefsteak", "2")))
    lines = [
        rules.judge(R(row("Okra", "3")), rules.Evidence(), handwritten_form=True),
        rules.judge(R(row("Squash", "4")), rules.Evidence(), handwritten_form=True),
    ]
    assert rules.compare_totals(["7"], lines) == [] and rules.compare_totals(["2"], lines) == []
    assert rules.compare_totals(["67"], lines) == ["TOTAL_DIFFERS:written=67,lines=2,sum=7"]
    # per-table sums explain a written number too; references and repeats are not totals
    lines[0].detail["table"], lines[1].detail["table"] = 1, 2
    assert rules.compare_totals(["3", "(4)", "SI#087622", "7"], lines) == []
    assert rules.compare_totals(["9", "9", "16 1/2"], lines) == [
        "TOTAL_DIFFERS:written=9/16.5,lines=2,sum=7"
    ]
    assert [ln.qty for ln in lines] == [D(3), D(4)]  # a written total never changes a line


def test_marks_stay_facts_about_their_own_row():
    R = rules.RowOut.model_validate
    ink = rules.Evidence(True, True)
    j = rules.judge(R(row("Okra", "2", old="3", no=True, chk=True, pkg="cs", wt="40", size="10 lb",
                          notes="small")), ink, handwritten_form=True)  # fmt: skip
    d = j.detail
    assert j.qty == D(2) and d["crossed_out"] == "3"  # replacement and crossed-out kept apart
    assert (d["pkg"], d["weight"], d["case_size"], d["notes"]) == ("cs", "40", "10 lb", "small")
    assert d["no_mark"] is True and d["checkmark"] is True
    assert d["supplied"] is None and d["unavailable"] is None  # not written = unknown, not 0
    assert {"CROSSED_OUT", "NO_MARK"} <= set(j.flags) and j.confidence < D("0.9")
    # crossed out with nothing written instead: no quantity, and NOT "unavailable"
    j = rules.judge(R(row("Okra", None, old="3", w=True)), ink, handwritten_form=True)
    assert j.qty is None and j.detail["crossed_out"] == "3" and j.detail["unavailable"] is None
    # a bare number in the package box beside the quantity goes to a person ("5" struck, "6" beside)
    j = rules.judge(R(row("Yulima Mango", "5", pkg="6")), ink, handwritten_form=True)
    assert j.qty == D(5) and "PKG_NUMBER" in j.flags and j.confidence < D("0.9")
    assert (
        "PKG_NUMBER"
        in rules.judge(R(row("Thai Chilli", "2", pkg="1/2")), ink, handwritten_form=True).flags
    )
    assert (
        "PKG_NUMBER"
        not in rules.judge(R(row("Okra", "2", pkg="cs")), ink, handwritten_form=True).flags
    )
    # a tick is not a delivery, and NO on one row says nothing about the next
    j = rules.judge(R(row("Squash", "4", chk=True)), ink, handwritten_form=True)
    assert j.detail["supplied"] is None and j.detail["no_mark"] is False and j.flags == []
    j = rules.judge(R(row("Squash", "4", sup="3", unav="1")), ink, handwritten_form=True)
    assert (j.qty, j.detail["supplied"], j.detail["unavailable"]) == (D(4), "3", "1")


def test_the_models_own_confidence_is_never_enough():
    R = rules.RowOut.model_validate
    sure = R(row("Okra", "12"))  # the model says it is sure
    assert rules.judge(sure, rules.Evidence(), handwritten_form=True).confidence < D("0.9")
    assert rules.judge(sure, rules.Evidence(True, True), handwritten_form=True).confidence >= D(
        "0.9"
    )
    j = rules.judge(sure, rules.Evidence(True, False), handwritten_form=True)
    assert j.flags == ["NO_INK_SEEN"] and j.confidence < D("0.9")
    j = rules.judge(sure, rules.Evidence(True, False, None, True), handwritten_form=True)
    assert j.flags == ["POSSIBLE_SHIFT"] and j.qty == D(12)  # flagged, never moved
    # writing in the next row that Gemini reported nowhere: this number may belong there
    j = rules.judge(sure, rules.Evidence(True, True, None, True), handwritten_form=True)
    assert j.flags == ["NEIGHBOUR_UNREAD"] and j.qty == D(12) and j.confidence < D("0.9")
    j = rules.judge(sure, rules.Evidence(True, True, "72"), handwritten_form=True)
    assert j.flags == ["LOCAL_READ_DIFFERS:72"] and j.qty == D(12)
    j = rules.judge(
        R(row("Okra", "12", unsure=True)), rules.Evidence(True, True), handwritten_form=True
    )
    assert "UNSURE" in j.flags and j.confidence < D("0.9")  # "unsure" can only lower a line
    # a second look: agreement settles a fraction, a difference is kept and flagged
    f = rules.judge(R(row("Okra", "1/2")), rules.Evidence(True, True), handwritten_form=True)
    f.detail["local_ink"] = True
    C = rules.CheckRow.model_validate
    rules.recheck(
        f, C({"i": 1, "item": "Okra", "qty": "1/2", "old": None, "no": False, "unsure": False})
    )
    assert f.confidence >= D("0.9") and f.flags == [] and f.qty == D("0.5")
    g = rules.judge(R(row("Okra", "1/2")), rules.Evidence(True, True), handwritten_form=True)
    rules.recheck(
        g, C({"i": 1, "item": "Okra", "qty": "15", "old": None, "no": False, "unsure": False})
    )
    assert g.qty == D("0.5") and "RECHECK_DIFFERS:15" in g.flags and g.confidence < D("0.9")


def test_supplier_is_never_the_customer_and_refs_compare_by_digits():
    assert rules.is_own_company("TAMISHA TRADING INC.") and rules.is_own_company("Tamisha")
    assert not rules.is_own_company("Island Grocers")
    assert rules.ref_key("SO#080778") == rules.ref_key("so 080778") == "080778"
    assert rules.ref_key("SO#080778") != rules.ref_key("SO#080781") and rules.ref_key("?") is None
    assert rules.ref_key("50#080781") == "080781"  # "SO" misread as "50" is still the same order


def test_deskew_levels_a_tilted_page():
    from PIL import Image, ImageDraw

    img = Image.new("L", (850, 1100), 255)
    d = ImageDraw.Draw(img)
    for y in range(100, 1000, 40):
        d.line([(60, y), (790, y)], fill=0, width=3)
    for x in (60, 300, 540, 790):
        d.line([(x, 100), (x, 980)], fill=0, width=3)
    assert gemini_ocr.deskew(img)[1] == 0.0
    tilted = img.rotate(2.0, resample=Image.Resampling.BICUBIC, fillcolor=255)
    _, angle = gemini_ocr.deskew(tilted)
    assert -2.4 <= angle <= -1.6  # turned back by about what it was tilted


# ------------------------------------------------------------------ limits, retries, breaker


def test_daily_cap_minute_limit_and_breaker(gemini, monkeypatch):
    schema, ok = {"type": "object"}, {"a": 1}
    ask = lambda **kw: gemini_client.call(
        "B", "s", [gemini_client.text_part("x")], schema, dict, **kw
    )  # noqa: E731
    g = gemini(ok)
    setenv(monkeypatch, GEMINI_DAILY_REQUEST_CAP="2")
    assert ask().data == ok and ask().total_tokens == 1500
    with pytest.raises(gemini_client.GeminiError) as e:
        ask()
    assert e.value.code == "GEMINI_DAILY_CAP" and len(g.sent) == 2  # the third was never sent
    b = gemini_client.budget()
    assert (b.cap, b.used_today, b.remaining_today) == (2, 2, 0)
    # per-minute limit: no slot inside the time allowed -> nothing is sent
    setenv(monkeypatch, GEMINI_DAILY_REQUEST_CAP="20", GEMINI_REQUESTS_PER_MINUTE="2")
    with pytest.raises(gemini_client.GeminiError) as e:
        ask(max_wait=1.0)
    assert e.value.code == "GEMINI_RATE_LIMIT" and e.value.transient and len(g.sent) == 2
    # Google's own daily quota answer stops the day, whatever our cap says
    with get_sessionmaker()() as db, db.begin():
        db.execute(delete(OcrEngineCall))
    setenv(monkeypatch, GEMINI_REQUESTS_PER_MINUTE="600")
    g = gemini((429, {"error": {"message": "Quota exceeded", "details": [
        {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}}))  # fmt: skip
    with pytest.raises(gemini_client.GeminiError) as e:
        ask()
    assert e.value.code == "GEMINI_QUOTA" and len(g.sent) == 1 and calls() == [("B", "QUOTA_DAY")]
    with pytest.raises(gemini_client.GeminiError) as e:
        ask()
    assert e.value.code == "GEMINI_DAILY_CAP" and len(g.sent) == 1
    # a slow-down answer is retried after a pause; an outage opens the breaker
    with get_sessionmaker()() as db, db.begin():
        db.execute(delete(OcrEngineCall))
    g = gemini((429, {"error": {"message": "slow down", "details": [{"retryDelay": "7s"}]}}), ok)
    assert ask().requests == 2 and calls() == [("B", "RATE_LIMITED"), ("B", "OK")]
    g = gemini((503, b"busy"))
    with pytest.raises(gemini_client.GeminiError) as e:
        ask()
    assert e.value.code == "GEMINI_UNAVAILABLE" and len(g.sent) == 2  # the call and its one retry
    for _ in range(2):  # the third failure in a row opens the breaker: nothing more is sent
        with pytest.raises(gemini_client.GeminiError) as e:
            ask()
    assert e.value.code == "GEMINI_CIRCUIT_OPEN" and len(g.sent) == 3
    assert gemini_client.budget().breaker_open_seconds > 0


def test_bad_answers_and_rejections(gemini):
    schema = {"type": "object"}
    ask = lambda v: gemini_client.call("B", "s", [gemini_client.text_part("x")], schema, v)  # noqa: E731
    g = gemini("this is not json", {"a": 1})
    assert ask(dict).data == {"a": 1} and calls() == [("B", "BAD_JSON"), ("B", "OK")]  # asked again
    g = gemini(page([{**row("Okra", "1"), "qty": 1}]))
    with pytest.raises(gemini_client.GeminiError) as e:
        ask(rules.validate_page)
    assert e.value.code == "GEMINI_BAD_JSON" and len(g.sent) == 2
    with get_sessionmaker()() as db, db.begin():
        db.execute(delete(OcrEngineCall))
    g = gemini((403, {"error": {"message": "API key not valid"}}))
    with pytest.raises(gemini_client.GeminiError) as e:
        ask(dict)
    assert e.value.code == "GEMINI_NOT_CONFIGURED" and len(g.sent) == 1  # never retried
    assert gemini_client.budget().used_today == 0  # turned away at the door: no allowance used
    assert KEY not in str(e.value) and KEY not in e.value.detail
    # what is sent: the key in one header, the configured model, schema output, not stored
    headers, body = g.sent[0]
    assert headers["x-goog-api-key"] == KEY and KEY not in json.dumps(body)
    assert g.urls[0].endswith("/v1beta/models/gemini-3.8-flash:generateContent")
    assert KEY not in g.urls[0] and "Api-Revision" not in headers
    assert body["generationConfig"]["responseFormat"] == {
        "text": {"mimeType": "APPLICATION_JSON", "schema": schema}
    }


def test_interactions_api_can_still_be_chosen(gemini, monkeypatch):
    setenv(monkeypatch, GEMINI_API="interactions")
    answer = {
        "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": '{"a": 1}'}]}],
        "usage": {"total_input_tokens": 7, "total_output_tokens": 3, "total_tokens": 10},
    }
    g = gemini((200, answer))
    a = gemini_client.call("B", "sys", [gemini_client.text_part("x")], {"type": "object"}, dict)
    assert a.data == {"a": 1} and a.total_tokens == 10
    headers, body = g.sent[0]
    assert g.urls[0].endswith("/v1beta/interactions") and headers["Api-Revision"]
    assert body["model"] == "gemini-3.8-flash" and body["store"] is False
    assert body["response_format"]["schema"] == {"type": "object"}
    # an answer Google cut short or blocked is not an answer, on either API
    g = gemini((200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}))
    setenv(monkeypatch, GEMINI_API="generate_content")
    with pytest.raises(gemini_client.GeminiError) as e:
        gemini_client.call("B", "sys", [gemini_client.text_part("x")], {"type": "object"}, dict)
    assert e.value.code == "GEMINI_NO_ANSWER" and calls()[-1] == ("B", "BLOCKED")


# ------------------------------------------------------------------ scan -> reading -> draft

# what "Google" answers for the synthetic sheet: the seven filled rows, read in different ways
ANSWER = page(
    [
        row("OKRA", "12"),
        row("TUMERIC", "5", pkg="cs", notes="fresh"),
        row("WIERRI PEPPERS", "1/2"),  # a fraction: always checked
        row("YELLOW YAM", "3", t=2, no=True),  # NO on this row only
        row("CALALLOO", "6", t=2, old="8", chk=True),  # 8 crossed out, 6 written
        row("SPICE MANGO", "2", t=3, unsure=True),
        # JAM GREEN BANANA (10 written) is left out by "Google"
        row("SQUASH", "7"),  # nothing is written on this row of the sheet
        row("BREADFRUIT"),  # printed row, nothing written
        row("TOTAL", "67", w=True),
    ],
    customer="Island Grocers", order_ref="SO#080778", doc_date="09-11-2026", totals=["38"],
)  # fmt: skip


# stage C, the doubted rows read once more from enlarged strips (in the order they were doubted)
SECOND_LOOK = {"checks": [
    {"i": 1, "item": "WIERRI PEPPERS", "qty": "1/2", "old": None, "no": False, "unsure": False},
    {"i": 2, "item": "CALALLOO", "qty": "6", "old": "8", "no": False, "unsure": False},
    {"i": 3, "item": "SPICE MANGO", "qty": "3", "old": None, "no": False, "unsure": False},
    {"i": 4, "item": "SQUASH", "qty": None, "old": None, "no": False, "unsure": False},
]}  # fmt: skip


@needs_tools
def test_scan_gemini_reading_then_editable_draft_only(owner, user, world, catalog, gemini):
    sales = user("sales")
    before = counts(world)
    g = gemini(ANSWER, SECOND_LOOK)
    pdf = paper_sheet.synthetic_scan(blocks(), FILLS, customer="Island Grocers", rotate=90)
    job = post_file(sales, world, pdf, "SALES_ORDER")
    assert job.status_code == 201, job.text
    assert drain(world) == 1
    ex = only(sales)
    eng = ex["engine"]
    assert ex["provider"] == "gemini" and ex["template"] == "TT-PAPER-1"
    assert (eng["provider"], eng["model"], eng["requests"], eng["fallback"]) == (
        "gemini", "gemini-3.8-flash", 2, None)  # fmt: skip
    assert eng["total_tokens"] == 3000 and eng["seconds"] >= 0 and eng["mode"] == "COMPACT"
    assert eng["pages"][0]["engine"] == "gemini" and eng["pages"][0]["turned"] in (90, 270)
    assert "raw" not in eng["pages"][0] and KEY not in json.dumps(ex)
    # one request: whole page + one enlarged crop per table, each announced, then the rules
    (_, body), (_, again) = g.sent
    parts, parts2 = body["contents"][0]["parts"], again["contents"][0]["parts"]
    assert sum("inline_data" in p for p in parts2) == 4  # four doubted rows, cut out
    assert again["generationConfig"]["responseFormat"]["text"]["schema"] == rules.CHECK_SCHEMA
    assert sum("inline_data" in p for p in parts) == 4 and "text" in parts[-1]
    assert "Table 1: " in parts[-1]["text"]
    assert "never" in body["system_instruction"]["parts"][0]["text"]
    assert ex["customer_name"] == "Island Grocers"  # exact, sure -> prefilled

    f = by_sku(ex)
    assert set(f) == {
        "OKR",
        "TUR",
        "WIR",
        "YYM",
        "CAL",
        "SPM",
        "SQU",
        "GBJ",
    }  # no Breadfruit, no TOTAL
    assert (f["OKR"]["read_qty"], f["OKR"]["status"]) == ("12.0000", "OK")  # ink found there too
    assert f["OKR"]["bbox"]["page"] == 1 and f["OKR"]["detail"]["local_ink"] is True
    assert f["TUR"]["detail"]["pkg"] == "cs" and f["TUR"]["detail"]["notes"] == "fresh"
    assert f["TUR"]["read_qty"] == "5.0000" and "cs" in f["TUR"]["raw_text"]
    # 1/2 is read as a half (not 1, 12 or 1.5) and the second look agrees; half a BOX still
    # needs a person, because boxes are whole
    assert (f["WIR"]["read_qty"], f["WIR"]["status"]) == ("0.5000", "LOW_CONFIDENCE")
    assert f["WIR"]["detail"]["recheck"]["same"] is True
    assert f["YYM"]["detail"]["no_mark"] is True and f["YYM"]["status"] == "LOW_CONFIDENCE"
    assert f["YYM"]["read_qty"] == "3.0000"  # ordered stays ordered; NO is for a person
    assert not any(x["detail"].get("no_mark") for k, x in f.items() if k != "YYM")
    assert (f["CAL"]["read_qty"], f["CAL"]["detail"]["crossed_out"]) == ("6.0000", "8")
    assert f["CAL"]["detail"]["checkmark"] is True and f["CAL"]["detail"]["supplied"] is None
    assert f["SPM"]["status"] == "LOW_CONFIDENCE" and "UNSURE" in f["SPM"]["detail"]["flags"]
    # the second look read 3 where the first read 2: both kept, nothing overwritten
    assert "RECHECK_DIFFERS:3" in f["SPM"]["detail"]["flags"] and f["SPM"]["read_qty"] == "2.0000"
    # a number where the local look sees an empty box is flagged, not trusted and not moved
    assert f["SQU"]["status"] == "LOW_CONFIDENCE" and f["SQU"]["read_qty"] == "7.0000"
    assert set(f["SQU"]["detail"]["flags"]) & {"NO_INK_SEEN", "POSSIBLE_SHIFT"}
    # writing Gemini said nothing about is kept for review
    assert f["GBJ"]["detail"]["flags"] == ["GEMINI_SAW_NOTHING"] and f["GBJ"]["status"] != "OK"
    assert any(w.startswith("TOTAL_DIFFERS:written=38/67,") for w in ex["warnings"])
    assert not any(w.startswith("GEMINI_FALLBACK") for w in ex["warnings"])

    # the admin page gets the validated answer and the local reading side by side
    a = owner.get(f"{P}/ocr/extractions/{ex['id']}/engine")
    assert a.status_code == 200, a.text
    a = a.json()
    assert a["engine"]["pages"][0]["raw"]["rows"][0]["item"] == "OKRA"
    assert (a["no_marks"], a["crossed_out"], a["lines"], a["uncertain"]) == (1, 1, 8, 6)
    assert {t["qty"] for t in a["tesseract_lines"]} >= {"12", "10"}
    assert any(v.startswith("RECHECK_DIFFERS") for v in a["validation_errors"])
    st = owner.get(f"{P}/ocr/engine")
    assert st.status_code == 200 and KEY not in st.text
    st = st.json()
    assert (st["provider"], st["model"], st["key_set"], st["data_consent"]) == (
        "gemini", "gemini-3.8-flash", True, True)  # fmt: skip
    assert (st["daily_request_cap"], st["used_today"], st["remaining_today"]) == (20, 2, 18)
    assert [c["stage"] for c in st["recent_calls"]] == ["C", "B"]
    assert sales.get(f"{P}/ocr/extractions/{ex['id']}/engine").status_code == 403
    assert sales.get(f"{P}/ocr/engine").status_code == 403  # company.manage only

    # review: an OK line may be confirmed, the rest need the reviewer's value
    ex = resolve(sales, ex, f["OKR"], "CONFIRM").json()
    assert code(resolve(sales, ex, f["SPM"], "CONFIRM")) == "NEEDS_CORRECTION"
    for k, x in f.items():
        if k == "OKR":
            continue
        if k == "SQU":
            r = resolve(sales, ex, x, "REMOVE", reason="nothing written on that row")
        else:
            r = resolve(sales, ex, x, "CORRECT", product_id=x["read_product_id"], unit_code="BOX",
                        qty="1" if k == "WIR" else x["read_qty"].split(".")[0],
                        reason="checked against the page")  # fmt: skip
        assert r.status_code == 200, r.text
        ex = r.json()
    acc = {"version": ex["version"], "customer_id": ex["customer_id"], "reviewed_all": True}
    r = sales.post(f"{P}/ocr/extractions/{ex['id']}/accept", json=acc, headers=idem())
    assert r.status_code == 200, r.text
    so = sales.get(f"{P}/sales-orders/{r.json()['sales_order_id']}").json()
    assert so["status"] == "DRAFT" and so["source"] == "SCAN" and not so.get("invoice_no")
    assert so["requested_for"] is None  # the date on a customer's sheet is not a delivery date
    got = {(ln["sku"], ln["ordered_qty"]) for ln in so["lines"]}
    assert got == {("OKR", "12.0000"), ("TUR", "5.0000"), ("WIR", "1.0000"), ("YYM", "3.0000"),
                   ("CAL", "6.0000"), ("SPM", "2.0000"), ("GBJ", "10.0000")}  # fmt: skip
    # the draft is reopened and edited like any other draft
    lines = [{"product_id": ln["product_id"], "unit_code": ln["unit_code"], "qty": "4"}
             for ln in so["lines"][:2]]  # fmt: skip
    body = {"version": so["version"], "customer_id": so["customer_id"],
            "warehouse_id": str(world.wh_a), "lines": lines}  # fmt: skip
    e = sales.put(f"{P}/sales-orders/{so['id']}", json=body, headers=idem())
    assert e.status_code == 200, e.text
    again = sales.get(f"{P}/sales-orders/{so['id']}").json()
    assert again["status"] == "DRAFT" and len(again["lines"]) == 2
    assert counts(world) == before  # no invoice, reservation or stock movement
    # the same file again is the same scan: Google is not asked twice
    again = post_file(sales, world, pdf, "SALES_ORDER")
    assert again.status_code in (200, 201) and again.json()["id"] == job.json()["id"]
    assert drain(world) == 0 and len(g.sent) == 2 and calls() == [("B", "OK"), ("C", "OK")]


@needs_tools
@pytest.mark.parametrize(
    "why, env, answer, sent",
    [
        ("GEMINI_CONSENT_NEEDED", {"GEMINI_DATA_CONSENT": "false"}, ANSWER, 0),
        ("GEMINI_NOT_CONFIGURED", {"GEMINI_API_KEY": ""}, ANSWER, 0),
        ("GEMINI_DAILY_CAP", {"GEMINI_DAILY_REQUEST_CAP": "0"}, ANSWER, 0),
        ("GEMINI_UNAVAILABLE", {}, (500, b"down"), 2),
        ("GEMINI_UNAVAILABLE", {}, TimeoutError("timed out"), 2),
        ("GEMINI_BAD_JSON", {}, "{not json", 2),
        ("GEMINI_QUOTA", {}, (429, {"error": {"message": "per day limit reached"}}), 1),
    ],
)
def test_falls_back_to_tesseract_and_says_so(user, world, catalog, gemini, monkeypatch, why, env,
                                             answer, sent):  # fmt: skip
    sales = user("sales")
    g = gemini(answer)
    setenv(monkeypatch, **env)
    pdf = paper_sheet.synthetic_scan(blocks(), FILLS, rotate=90)
    assert post_file(sales, world, pdf, "SALES_ORDER").status_code == 201
    assert drain(world) == 1
    ex = only(sales)  # the reading is there to review: the job did not fail
    assert ex["provider"] == "gemini" and f"GEMINI_FALLBACK:{why}" in ex["warnings"]
    assert ex["engine"]["provider"] == "tesseract" and ex["engine"]["fallback"] == why
    assert len(g.sent) == sent and ex["engine"]["requests"] == sent  # failed requests count too
    got = {(f["read_sku"], f["read_qty"]) for f in ex["fields"]}
    assert ("OKR", "12.0000") in got and len(got) == 7
    assert {f["status"] for f in ex["fields"]} == {"LOW_CONFIDENCE"}  # Tesseract's handwriting


@needs_tools
def test_two_stores_in_one_file_are_not_merged(user, world, catalog, gemini, tmp_path):
    if not shutil.which("pdfunite"):
        pytest.skip("no pdfunite")
    sales = user("sales")
    p1 = page([row("OKRA", "12")], customer="Island Grocers", order_ref="SO#080778")
    p2 = page(
        [row("OKRA", "12")], customer="Sarangan Miss", cust_unsure=True, order_ref="SO#080781"
    )

    gemini(p1, p2)
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    a.write_bytes(paper_sheet.synthetic_scan(blocks(), {"Okra": "12"}, rotate=90))
    b.write_bytes(paper_sheet.synthetic_scan(blocks(), {"Okra": "12"}, rotate=0))
    subprocess.run(["pdfunite", str(a), str(b), str(tmp_path / "ab.pdf")], check=True)
    both = (tmp_path / "ab.pdf").read_bytes()
    assert post_file(sales, world, both, "SALES_ORDER").status_code == 201
    assert drain(world) == 1
    ex = only(sales)
    assert ex["page_count"] == 2 and ex["customer_id"] is None  # two stores: nobody is prefilled
    assert any(w.startswith("MIXED_ORDERS:") for w in ex["warnings"])
    assert sorted(f["detail"]["page"] for f in ex["fields"]) == [1, 2]  # both kept, by page
    assert {tuple(c["pages"]) for c in ex["engine"]["customers"]} == {(1,), (2,)}


@needs_tools
@pytest.mark.parametrize(
    "customer, unsure, warning",
    [
        ("Tamisha Trading Inc", False, "CUSTOMER_IS_SUPPLIER"),
        ("Island Grocers", True, "CUSTOMER_UNSURE:Island Grocers"),
        ("Iland Grocer", False, "CUSTOMER_NOT_FOUND:Iland Grocer"),
        (None, False, "CUSTOMER_NOT_READ"),
    ],
)
def test_customer_is_prefilled_only_when_sure(
    user, world, catalog, gemini, customer, unsure, warning
):
    sales = user("sales")
    gemini(page([row("OKRA", "12")], customer=customer, cust_unsure=unsure))
    pdf = paper_sheet.synthetic_scan(blocks(), {"Okra": "12"}, rotate=0)
    assert post_file(sales, world, pdf, "SALES_ORDER").status_code == 201
    assert drain(world) == 1
    ex = only(sales)
    assert ex["customer_id"] is None and warning in ex["warnings"]  # the raw text is shown


@needs_tools
def test_supplier_invoice_becomes_a_receiving_draft(owner, world, setup, vendor, gemini,
                                                    tmp_path):  # fmt: skip
    from sqlalchemy import text

    lines = [row("Cassava frozen", "25", t=0, pkg="box", price="12.50", amt="312.50"),
             row("Yellow yam", "30", t=0, pkg="lb", price="7.75", amt="232.50"),
             row("Total", None, t=0, amt="545.00")]  # fmt: skip
    gemini(page(lines, doc_type="VENDOR_RECEIVING", issuer="Accra Roots Exports", doc_no="ARX-9001",
                doc_date="2026-11-02", currency="USD", total="545.00"))  # fmt: skip
    png = scan(invoice_pdf(no="ARX-9001"), tmp_path)
    q = f"warehouse_id={world.wh_a}&job_type=VENDOR_DOCUMENT&filename=inv.png"
    h = {**owner._h(idem()), "Content-Type": "image/png"}
    assert owner.c.post(f"{P}/scans/uploads?{q}", content=png, headers=h).status_code == 201
    assert drain(world) == 1
    ex = only(owner)
    assert ex["engine"]["provider"] == "gemini" and ex["engine"]["doc_type"] == "VENDOR_RECEIVING"
    assert ex["header"]["document_no"]["value"] == "ARX-9001" and ex["vendor_id"] == vendor["id"]
    assert [(f["read_sku"], f["read_qty"], f["read_unit_price"]) for f in ex["fields"]] == [
        ("CAS", "25.0000", "12.5000"), ("YAM", "30.0000", "7.7500")]  # fmt: skip
    # Gemini and the local reader agree on product and quantity: these may be confirmed as read
    assert {f["status"] for f in ex["fields"]} == {"OK"}
    for f in ex["fields"]:
        r = resolve(owner, ex, f, "CONFIRM")
        assert r.status_code == 200, r.text
        ex = r.json()
    body = {"version": ex["version"], "vendor_id": vendor["id"], "document_no": "ARX-9001",
            "reviewed_all": True}  # fmt: skip
    r = owner.post(f"{P}/ocr/extractions/{ex['id']}/to-receiving", json=body, headers=idem())
    assert r.status_code == 200, r.text
    with get_sessionmaker()() as db:
        c = {"c": world.company_id}
        assert db.scalar(text("SELECT count(*) FROM stock_movements WHERE company_id = :c"), c) == 0
        status = db.scalar(text("SELECT status FROM shipments WHERE company_id = :c"), c)
    assert status == "OPEN"  # an open receiving draft: it waits for the warehouse, nothing posted


@needs_tools
@pytest.mark.skipif(
    os.environ.get("GEMINI_LIVE_TEST") != "1", reason="real Google call: set GEMINI_LIVE_TEST=1"
)
def test_live_gemini_reads_a_synthetic_sheet(user, world, catalog, monkeypatch):
    """The only test that talks to Google. It sends one SYNTHETIC sheet (no customer data), with
    the key from the environment (apps/api/.env), and uses one request of the day's allowance."""
    setenv(monkeypatch, OCR_PROVIDER="gemini", GEMINI_DATA_CONSENT="true",
           GEMINI_DAILY_REQUEST_CAP="3")  # fmt: skip
    if not gemini_client.configured():
        pytest.skip("GEMINI_API_KEY is not set")
    with get_sessionmaker()() as db, db.begin():
        db.execute(delete(OcrEngineCall))
    sales = user("sales")
    try:
        pdf = paper_sheet.synthetic_scan(blocks(), FILLS, customer="Island Grocers", rotate=90)
        assert post_file(sales, world, pdf, "SALES_ORDER").status_code == 201
        assert drain(world) == 1
    finally:
        setenv(monkeypatch, OCR_PROVIDER="fake")
    ex = only(sales)
    assert ex["engine"]["provider"] == "gemini", ex["warnings"]
    got = {(f["read_sku"], f["read_qty"]) for f in ex["fields"]}
    want = {("OKR", "12.0000"), ("TUR", "5.0000"), ("WIR", "4.0000"), ("YYM", "3.0000"),
            ("CAL", "6.0000"), ("SPM", "2.0000"), ("GBJ", "10.0000")}  # fmt: skip
    print("LIVE", ex["engine"]["seconds"], ex["engine"]["total_tokens"], sorted(got ^ want))
    assert len(got & want) >= 6
