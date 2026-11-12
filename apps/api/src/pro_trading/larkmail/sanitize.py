"""Email HTML reduced to an allow-list (untrusted input: decoded, bounded and rebuilt, never
interpreted). Unlike the Gmail Inbox sanitizer this one keeps images, but never loads them by
itself: a remote picture becomes <img data-remote-src="..."> with no src, so nothing is fetched
until the reader presses "Load images" (render(..., remote_images=True)). Pictures carried inside
the email (cid:) are shown from the stored attachment as a data: URL.
No scripts, styles sheets, event handlers, forms, frames or CSS urls survive."""

from __future__ import annotations

import base64
import html
import re
from collections.abc import Callable
from html.parser import HTMLParser

_ALLOWED = {
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "code", "col", "colgroup", "dd",
    "div", "dl", "dt", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li",
    "ol", "p", "pre", "s", "small", "span", "strike", "strong", "sub", "sup", "table", "tbody",
    "td", "tfoot", "th", "thead", "tr", "u", "ul",
}  # fmt: skip
_VOID = {"br", "hr", "img", "col"}
_DROP = {
    "script", "style", "head", "title", "iframe", "object", "embed", "form", "svg", "math",
    "noscript", "template", "link", "meta", "base", "audio", "video", "canvas", "select",
    "textarea", "button", "input", "frame", "frameset", "applet",
}  # fmt: skip
_DROP_VOID = {"link", "meta", "base", "input"}
_SAFE_HREF = re.compile(r"^(https?://|mailto:)[^\s\"'<>`]{1,2000}$", re.IGNORECASE)
_SAFE_IMG = re.compile(r"^https?://[^\s\"'<>`]{1,2000}$", re.IGNORECASE)
_BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "hr"}
# Presentation attributes kept verbatim when their value is plain.
_ATTRS = {"align", "valign", "width", "height", "colspan", "rowspan", "border", "cellpadding",
          "cellspacing", "bgcolor", "color", "alt", "title", "dir"}  # fmt: skip
_PLAIN = re.compile(r"^[#\w%.,\- ]{0,64}$")
# Inline CSS: only a small set of properties, and never anything that can fetch or run.
_CSS_PROPS = {
    "color", "background-color", "font-weight", "font-style", "font-size", "font-family",
    "text-align", "text-decoration", "padding", "padding-left", "padding-right", "padding-top",
    "padding-bottom", "margin", "margin-left", "margin-right", "margin-top", "margin-bottom",
    "border", "border-collapse", "border-top", "border-bottom", "border-left", "border-right",
    "width", "max-width", "height", "line-height", "vertical-align", "white-space",
}  # fmt: skip
_CSS_BAD = re.compile(r"url\s*\(|expression|javascript:|@import|behavior|\\", re.IGNORECASE)
_CSS_VALUE = re.compile(r"^[#\w%.,\-\s'\"()/]{0,120}$")
_WS = re.compile(r"[ \t\r\f\v]+")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

BLOCKED_IMG = "data-remote-src"


def _style(raw: str) -> str:
    out = []
    for decl in raw.split(";"):
        if ":" not in decl:
            continue
        prop, val = (x.strip() for x in decl.split(":", 1))
        prop = prop.lower()
        if prop in _CSS_PROPS and not _CSS_BAD.search(val) and _CSS_VALUE.match(val):
            out.append(f"{prop}: {val}")
    return "; ".join(out)


class _Sanitizer(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.text: list[str] = []
        self.size = 0
        self.limit = limit
        self.truncated = False
        self.drop_depth = 0
        self.stack: list[str] = []
        self.remote_images = False

    def _emit(self, s: str) -> None:
        if self.truncated:
            return
        if self.size + len(s) > self.limit:
            self.truncated = True
            return
        self.out.append(s)
        self.size += len(s)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP and tag not in _DROP_VOID:
                self.drop_depth += 1
            return
        if tag in _DROP:
            if tag not in _DROP_VOID:
                self.drop_depth = 1
            return
        if tag in _BLOCK:
            self.text.append("\n")
        if tag not in _ALLOWED:
            return
        kept: list[str] = []
        for k, v in attrs:
            k = (k or "").lower()
            v = (v or "").strip()
            if tag == "a" and k == "href" and _SAFE_HREF.match(v):
                kept.append(f'href="{html.escape(v, quote=True)}"')
            elif tag == "img" and k == "src":
                if v.lower().startswith("cid:"):
                    kept.append(f'data-cid="{html.escape(v[4:].strip("<>"), quote=True)}"')
                elif _SAFE_IMG.match(v):
                    self.remote_images = True
                    kept.append(f'{BLOCKED_IMG}="{html.escape(v, quote=True)}"')
            elif k == "style":
                css = _style(v)
                if css:
                    kept.append(f'style="{html.escape(css, quote=True)}"')
            elif k in _ATTRS and _PLAIN.match(v):
                kept.append(f'{k}="{html.escape(v, quote=True)}"')
        if tag == "a":
            kept.append('target="_blank" rel="noopener noreferrer nofollow"')
        self._emit(f"<{tag}{' ' if kept else ''}{' '.join(kept)}>")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.drop_depth:
            if tag in _DROP:
                self.drop_depth -= 1
            return
        if tag in _BLOCK:
            self.text.append("\n")
        if tag in _ALLOWED and tag not in _VOID and tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self._emit(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self.drop_depth:
            return
        self._emit(html.escape(data, quote=False))
        self.text.append(data)

    def result(self) -> str:
        while self.stack:
            self._emit(f"</{self.stack.pop()}>")
        return "".join(self.out)


def tidy(text: str) -> str:
    lines = [_WS.sub(" ", ln).strip() for ln in _CTRL.sub("", text).split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def sanitize(raw: str, limit: int) -> tuple[str, str, bool, bool]:
    """(safe HTML, plain text, truncated, has remote images)."""
    p = _Sanitizer(limit)
    try:
        p.feed(raw)
        p.close()
    except Exception:  # noqa: BLE001 - malformed mail must not stop the sync
        return "", "", True, False
    return p.result(), tidy("".join(p.text)), p.truncated, p.remote_images


_REMOTE = re.compile(rf'{BLOCKED_IMG}="([^"]*)"')
_CID = re.compile(r'data-cid="([^"]*)"')


def render(
    safe_html: str, *, remote_images: bool, cid_lookup: Callable[[str], tuple[str, bytes] | None]
) -> str:
    """Stored (already safe) HTML for display. cid pictures become data: URLs from the stored
    attachment; remote ones get their src back only when the reader asked for them."""

    def cid(m: re.Match[str]) -> str:
        got = cid_lookup(html.unescape(m.group(1)))
        if not got:
            return 'alt="[picture]"'
        mime, data = got
        return f'src="data:{mime};base64,{base64.b64encode(data).decode()}"'

    out = _CID.sub(cid, safe_html)
    if remote_images:
        out = _REMOTE.sub(lambda m: f'src="{m.group(1)}" referrerpolicy="no-referrer"', out)
    return out


def text_to_html(text: str) -> str:
    return "<div>" + html.escape(text).replace("\n", "<br>") + "</div>"
