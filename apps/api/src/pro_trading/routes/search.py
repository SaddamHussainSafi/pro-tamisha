"""Universal search (⌘K palette and /search page). Read-only: nothing here changes data except
the employee's own private "recently opened" list."""

import base64
import datetime as dt
import json
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert

from .. import ratelimit
from ..deps import DbDep, Principal, PrincipalDep, deny
from ..errors import ApiError
from ..search import engine
from ..search.models import SearchRecent
from ..search.parser import QueryError, parse
from ..search.preview import build_preview, load_visible
from ..search.registry import DOC_TYPES, MISSING_MODULES, Visibility, visibility

router = APIRouter(prefix="/search", tags=["search"])

RATE_PER_MINUTE = 240  # a fast typist with 150 ms debounce stays far below this
RECENT_KEEP = 20


class SearchHitOut(BaseModel):
    type: str
    id: str
    title: str
    subtitle: str
    url: str
    status: str | None = None
    date: dt.date | None = None
    amount: str | None = None  # decimal as text; only when the employee may see amounts
    currency: str | None = None
    match: str
    snippet: str | None = None


class SearchGroupOut(BaseModel):
    type: str
    label: str
    count: int
    items: list[SearchHitOut]


class IndexStatusOut(BaseModel):
    degraded: bool  # search table unavailable: exact numbers only
    pending: int  # changes waiting to be indexed
    behind_seconds: float | None


class SearchOut(BaseModel):
    q: str
    terms: list[str]
    filters: list[list[str]]
    types: list[dict[str, str]]  # categories this employee may search
    groups: list[SearchGroupOut]
    items: list[SearchHitOut]
    total: int
    total_capped: bool
    next_cursor: str | None
    index: IndexStatusOut
    notes: list[str]
    took_ms: float


class PreviewOut(BaseModel):
    type: str
    id: str
    title: str
    subtitle: str
    url: str
    fields: list[dict[str, str]]
    related: list[dict[str, Any]]
    actions: list[dict[str, str]]


class RecentIn(BaseModel):
    type: str
    id: uuid.UUID


class RecentOut(BaseModel):
    type: str
    id: str
    title: str
    subtitle: str
    url: str


def _limit(request: Request, employee_id: uuid.UUID) -> None:
    if ratelimit.attempt("search", str(employee_id), 60) > RATE_PER_MINUTE:
        raise ApiError(429, "RATE_LIMITED", "Too many searches; wait a few seconds")


def _visible(request: Request, p: Principal) -> Visibility:
    """Search is for people who may read at least one kind of record; nobody else gets in."""
    vis = visibility(p)
    if not vis.types:
        raise deny(request, p, "catalog.read", None)
    return vis


def _cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def _offset(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        o = int(json.loads(base64.urlsafe_b64decode(cursor.encode()))["o"])
    except (ValueError, KeyError, TypeError):
        raise ApiError(422, "VALIDATION", "Invalid cursor") from None
    if not 0 <= o <= engine.CANDIDATE_CAP:
        raise ApiError(422, "VALIDATION", "Invalid cursor")
    return o


def _out(h: engine.Hit, money: frozenset[str], snippet: str | None = None) -> SearchHitOut:
    show_amount = h.amount is not None and h.doc_type in money
    return SearchHitOut(
        type=h.doc_type,
        id=str(h.source_id),
        title=h.title,
        subtitle=h.subtitle,
        url=h.url,
        status=h.status,
        date=h.doc_date,
        amount=f"{h.amount:.2f}" if show_amount else None,
        currency=h.currency if show_amount else None,
        match=engine.MATCH_LABEL.get(h.tier, "match"),
        snippet=snippet,
    )


@router.get("", response_model=SearchOut)
def search(
    request: Request,
    db: DbDep,
    p: PrincipalDep,
    q: Annotated[str, Query(max_length=200)] = "",
    type: Annotated[str | None, Query(max_length=200, description="comma-separated types")] = None,  # noqa: A002
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    per_group: Annotated[int, Query(ge=0, le=10, description="items per group (palette)")] = 0,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    sort: Annotated[str, Query(pattern="^(relevance|newest|oldest)$")] = "relevance",
) -> SearchOut:
    _limit(request, p.employee.id)
    try:
        pq = parse(q)
    except QueryError as exc:
        raise ApiError(422, "BAD_QUERY", str(exc)) from None
    if type:
        wanted = {t.strip() for t in type.split(",") if t.strip()}
        if not wanted <= set(DOC_TYPES):
            raise ApiError(422, "VALIDATION", "Unknown type")
        pq.narrow(wanted)
    vis = _visible(request, p)
    allowed = [{"type": t, "label": DOC_TYPES[t].label} for t in vis.types]
    if not pq.needle and not pq.has_filters and pq.types is None:
        pending, behind = engine.queue_lag(db)
        return SearchOut(
            q=q, terms=[], filters=[], types=allowed, groups=[], items=[], total=0,
            total_capped=False, next_cursor=None,
            index=IndexStatusOut(degraded=False, pending=pending, behind_seconds=behind),
            notes=[], took_ms=0,
        )  # fmt: skip
    res = engine.search(db, p, vis, pq)
    hits = res.hits
    if sort != "relevance":
        hits = sorted(hits, key=lambda h: h.doc_date or date.min, reverse=sort == "newest")
    off = _offset(cursor)
    page = hits[off : off + limit]
    groups: list[SearchGroupOut] = []
    if per_group:
        by_type: dict[str, list[engine.Hit]] = {}
        for h in hits:
            by_type.setdefault(h.doc_type, []).append(h)
        order = sorted(by_type, key=lambda t: (-by_type[t][0].tier, DOC_TYPES[t].rank))
        for t in order:
            groups.append(
                SearchGroupOut(
                    type=t,
                    label=DOC_TYPES[t].label,
                    count=res.counts[t],
                    items=[
                        _out(h, vis.money, engine.snippet(db, h, res.terms))
                        for h in by_type[t][:per_group]
                    ],
                )  # fmt: skip
            )
    items = [_out(h, vis.money, engine.snippet(db, h, res.terms)) for h in page]
    nxt = off + limit
    return SearchOut(
        q=q,
        terms=res.terms,
        filters=[[k, v] for k, v in pq.filters],
        types=allowed,
        groups=groups,
        items=items,
        total=len(hits),
        total_capped=res.capped,
        next_cursor=_cursor(nxt) if nxt < len(hits) else None,
        index=IndexStatusOut(
            degraded=res.degraded, pending=res.queue_pending, behind_seconds=res.behind_seconds
        ),
        notes=res.notes,
        took_ms=res.took_ms,
    )


@router.get("/preview/{doc_type}/{rid}", response_model=PreviewOut)
def preview(
    doc_type: str, rid: uuid.UUID, request: Request, db: DbDep, p: PrincipalDep
) -> PreviewOut:
    _limit(request, p.employee.id)
    if doc_type not in DOC_TYPES or doc_type in ("activity", "email"):
        raise ApiError(404, "NOT_FOUND", "Not found")
    pv = build_preview(db, p, _visible(request, p), doc_type, rid)
    return PreviewOut(
        type=pv.doc_type, id=str(pv.id), title=pv.title, subtitle=pv.subtitle, url=pv.url,
        fields=pv.fields, related=pv.related, actions=pv.actions,
    )  # fmt: skip


@router.get("/recent", response_model=list[RecentOut])
def recent(request: Request, db: DbDep, p: PrincipalDep) -> list[RecentOut]:
    """The employee's own recently opened records that still exist and that they may still see."""
    vis = _visible(request, p)
    rows = db.execute(
        text(
            "SELECT r.doc_type, r.source_id, d.title, d.subtitle, d.url, d.warehouse_id"
            " FROM search_recent r JOIN search_documents d"
            "   ON d.doc_type = r.doc_type AND d.source_id = r.source_id"
            "   AND d.company_id = r.company_id"
            " WHERE r.employee_id = :e AND r.company_id = :c ORDER BY r.opened_at DESC LIMIT :n"
        ),
        {"e": p.employee.id, "c": p.company_id, "n": RECENT_KEEP},
    ).all()
    return [
        RecentOut(type=t, id=str(i), title=ti, subtitle=s, url=u)
        for t, i, ti, s, u, w in rows
        if vis.can_see(t, w)
    ]


@router.post("/recent", status_code=204)
def add_recent(body: RecentIn, request: Request, db: DbDep, p: PrincipalDep) -> Response:
    if body.type == "email":
        return Response(status_code=204)  # private mail is never kept in a shared recent list
    if body.type not in DOC_TYPES or body.type == "activity":
        raise ApiError(422, "VALIDATION", "Unknown type")
    load_visible(db, p, _visible(request, p), body.type, body.id)  # 404 unless visible
    stmt = insert(SearchRecent).values(
        employee_id=p.employee.id, company_id=p.company_id, doc_type=body.type, source_id=body.id
    )
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=["employee_id", "doc_type", "source_id"], set_={"opened_at": func.now()}
        )
    )
    keep = (
        select(SearchRecent.opened_at)
        .where(SearchRecent.employee_id == p.employee.id)
        .order_by(SearchRecent.opened_at.desc())
        .offset(RECENT_KEEP - 1)
        .limit(1)
        .scalar_subquery()
    )
    db.execute(
        delete(SearchRecent).where(
            SearchRecent.employee_id == p.employee.id, SearchRecent.opened_at < keep
        )
    )
    db.commit()
    return Response(status_code=204)


@router.delete("/recent", status_code=204)
def clear_recent(request: Request, db: DbDep, p: PrincipalDep) -> Response:
    _visible(request, p)
    db.execute(delete(SearchRecent).where(SearchRecent.employee_id == p.employee.id))
    db.commit()
    return Response(status_code=204)


class StatusOut(BaseModel):
    types: list[dict[str, str]]
    missing_modules: dict[str, str]
    index: IndexStatusOut


@router.get("/status", response_model=StatusOut)
def status(request: Request, db: DbDep, p: PrincipalDep) -> StatusOut:
    vis = _visible(request, p)
    pending, behind = engine.queue_lag(db)
    return StatusOut(
        types=[{"type": t, "label": DOC_TYPES[t].label} for t in vis.types],
        missing_modules=MISSING_MODULES,
        index=IndexStatusOut(degraded=False, pending=pending, behind_seconds=behind),
    )
