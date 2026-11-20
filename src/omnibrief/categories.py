"""Watchlist categories: one per VULNS_WATCHLIST entry, plus the built-in "general" (everything).

Syntax (comma-separated entries):
    microsoft, oracle, google                         one term each; label = the term
    programming languages=python|java|npm|pypi|rust    a label matching any of several terms

Matching is case-insensitive on whole words against a record's title, description, products and
packages ("ecosystem name"). The same categories drive the inclusion filter (any term counts as a
watchlist hit), the per-category Atom feeds and the dashboard's dropdown. Categories are computed
at render time from the *current* variable, so changing it re-sorts everything still on display.

NOTE: categories are public — their labels appear in feed URLs and on the dashboard.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property

from omnibrief.config import TopicConfig
from omnibrief.models import DigestItem, Vuln

GENERAL = "general"
RESERVED = {GENERAL, "global-critical"}  # built-in views; watchlist entries cannot reuse them
MATCH_TEXT_CHARS = 1500


def slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


@dataclass
class Category:
    slug: str
    label: str
    terms: list[str] = field(default_factory=list)

    @cached_property
    def pattern(self) -> re.Pattern | None:
        return compile_terms(self.terms)

    def matches(self, text: str) -> bool:
        return self.pattern is not None and self.pattern.search(text) is not None

    def matches_item(self, item: DigestItem) -> bool:
        return self.matches(item_text(item))


def compile_terms(terms: list[str]) -> re.Pattern | None:
    terms = sorted({t for t in terms if t}, key=len, reverse=True)
    if not terms:
        return None
    alts = "|".join(re.escape(t) for t in terms)
    return re.compile(rf"(?<![\w-])(?:{alts})(?![\w-])", re.IGNORECASE)


def parse_categories(raw: str | None) -> list[Category]:
    """Parse VULNS_WATCHLIST into categories, in the order given. Duplicate labels merge;
    a `general` entry is ignored (it is built in)."""
    out: dict[str, Category] = {}
    for entry in (raw or "").split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" in entry:
            label, _, terms_raw = entry.partition("=")
            terms = [t.strip() for t in terms_raw.split("|") if t.strip()]
        else:
            label, terms = entry, [entry]
        label = " ".join(label.split())
        slug = slugify(label)
        if not slug or slug in RESERVED or not terms:
            continue
        cat = out.setdefault(slug, Category(slug=slug, label=label))
        cat.terms.extend(t for t in terms if t not in cat.terms)
    return list(out.values())


def categories_for(topic: TopicConfig, env: Mapping[str, str]) -> list[Category]:
    """Built-in Global Critical first, then the watchlist categories in the order given."""
    from omnibrief.global_critical import GLOBAL_CRITICAL  # that module imports this one

    name = topic.filters.watchlist_env
    return [GLOBAL_CRITICAL, *parse_categories(env.get(name, "") if name else "")]


def vuln_text(v: Vuln) -> str:
    parts = [v.title, v.description, *v.products]
    parts += [f"{p.ecosystem} {p.name}" for p in v.packages]
    return "\n".join(p for p in parts if p)


def item_text(item: DigestItem) -> str:
    return item.match_text or f"{item.title}\n{item.product}"


def item_categories(item: DigestItem, categories: list[Category]) -> list[str]:
    return [c.slug for c in categories if c.matches_item(item)]
