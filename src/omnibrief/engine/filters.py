"""Declarative topic filters (design-v2 §5.2 FILTER, before any LLM call).

YAML under `filters:` — every condition at one level must hold (AND):
  min_fields:   {magnitude: 4.0}                 numeric field >= value
  in_fields:    {notifying_country: [Austria]}   field (or any element of a list field) in list; "all" = no-op
  min_severity: medium                           item severity rank >= this
  keywords_any: [llm, agent]                     whole-word match in title/summary (case-insensitive)
  keywords_env: ARXIV_KEYWORDS                   extra comma-separated keywords from an env var (kept private)
  any_of:       [ {…}, {…} ]                     at least one nested condition set holds
  always_include: {…}                            if this nested set holds, include regardless of the rest
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from omnibrief.categories import compile_terms
from omnibrief.engine.items import Item, sev_rank


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else [v]


def _keywords(cond: dict, env: Mapping[str, str]) -> list[str]:
    words = [str(w) for w in cond.get("keywords_any") or []]
    if name := cond.get("keywords_env"):
        words += [w.strip() for w in (env.get(name) or "").split(",") if w.strip()]
    return words


def matches(item: Item, cond: dict, env: Mapping[str, str]) -> bool:
    for name, minimum in (cond.get("min_fields") or {}).items():
        value = item.fields.get(name)
        if value is None or float(value) < float(minimum):
            return False
    for name, allowed in (cond.get("in_fields") or {}).items():
        if allowed == "all" or allowed == ["all"]:
            continue
        values = {str(v).lower() for v in _as_list(item.fields.get(name))}
        if not values & {str(a).lower() for a in _as_list(allowed)}:
            return False
    if (ms := cond.get("min_severity")) and sev_rank(item.severity) < sev_rank(ms):
        return False
    words = _keywords(cond, env)
    if words or cond.get("keywords_env"):
        pattern = compile_terms(words)
        if pattern is None or not pattern.search(f"{item.title}\n{item.summary or ''}"):
            return False
    alts = cond.get("any_of")
    return not alts or any(matches(item, a, env) for a in alts)


def passes(item: Item, filters: dict, env: Mapping[str, str]) -> bool:
    always = filters.get("always_include")
    if always and matches(item, always, env):
        return True
    rest = {k: v for k, v in filters.items() if k != "always_include"}
    return matches(item, rest, env)
