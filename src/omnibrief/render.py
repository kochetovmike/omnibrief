"""Markdown → sanitised HTML, shared by every publisher (design §7).

Advisory text and LLM output are untrusted. Everything rendered goes through `sanitise`:
nh3 tag allowlist + a link allowlist (only URLs that appeared in the input records, or our own
site). Links to anything else are stripped (the text stays, the href goes). The visible text of
every surviving link is replaced by its hostname, so injected text can't dress up a reference
URL as e.g. "Official patch".
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

import markdown as md_lib
import nh3

ALLOWED_TAGS = {"p", "ul", "ol", "li", "a", "strong", "em", "code", "h3"}
_MD_SPECIAL = re.compile(r"([\\`*_\[\]()#])")
_ANCHOR = re.compile(r'<a href="([^"]*)"([^>]*)>(.*?)</a>', re.DOTALL)


def escape_md(text: str) -> str:
    """Escape record text before we put it into markdown we generate ourselves."""
    text = " ".join(text.split())
    text = _MD_SPECIAL.sub(r"\\\1", text)
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _norm(url: str) -> str:
    return html.unescape(url).strip()


def _host_label(match: re.Match, keep_text: set[str]) -> str:
    href, attrs, _text = match.groups()
    url = _norm(href)
    if url in keep_text:
        return match.group(0)
    label = urlsplit(url).hostname or "link"
    return f'<a href="{href}"{attrs}>{html.escape(label)}</a>'


def sanitise(markdown_text: str, allowed_urls: set[str], keep_text_for: set[str] = frozenset()) -> str:
    """`keep_text_for`: our own URLs whose link text we wrote ourselves and may keep."""
    allowed = {_norm(u) for u in allowed_urls}
    keep = {_norm(u) for u in keep_text_for}
    raw_html = md_lib.markdown(markdown_text, output_format="html")

    def attribute_filter(tag: str, attr: str, value: str) -> str | None:
        if tag == "a" and attr == "href":
            return value if _norm(value) in allowed else None
        return value

    cleaned = nh3.clean(
        raw_html,
        tags=ALLOWED_TAGS,
        attributes={"a": {"href"}},
        url_schemes={"https", "http"},
        link_rel="noopener noreferrer nofollow",
        attribute_filter=attribute_filter,
        strip_comments=True,
    ).strip()
    return _ANCHOR.sub(lambda m: _host_label(m, keep), cleaned)


def html_to_text(fragment: str) -> str:
    """Plain text from sanitised HTML (for summaries/snippets)."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())
