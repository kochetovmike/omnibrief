"""Primary state store (`git_branch`, design-v2 §7): JSON files in the checked-out `data` branch.

Extends the v1 Store (which the vulns plugin keeps using unchanged) with the v2 layout:
  state/schedule.json                     topic → last_attempt / last_success / status
  state/llm_budget.json                   {"date": "YYYY-MM-DD", "calls": n}  (UTC day)
  state/seen/<topic>.json                 key → {first_seen, last_seen, material_hash}  (generic topics)
  state/deliveries/<channel>.json         push-channel delivery records (keyed "<topic>:<key>")
  state/engine_health.json                last engine health (for render-only)
  items/<topic>/<yyyy>/<mm>/<runId>.json  items emitted by a run (alert topics; 90 days)
  digests/<topic>/<yyyy>/<mm>/<runId>.json  TopicDigest (generic digest topics) / Digest (vulns)
  runs/<yyyy>/<mm>/<runId>-engine.json    engine run record
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from omnibrief.engine.items import Item, TopicDigest
from omnibrief.state import Store, _write_json

ITEMS_KEEP_DAYS = 90


class EngineStore(Store):
    # --- small JSON documents ---------------------------------------------------------------------
    def _load(self, *parts: str) -> dict:
        path = self.root.joinpath(*parts)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def _save(self, data: dict, *parts: str) -> None:
        _write_json(self.root.joinpath(*parts), json.dumps(data, indent=1, sort_keys=True, default=str))

    def load_schedule(self) -> dict:
        return self._load("state", "schedule.json")

    def save_schedule(self, schedule: dict) -> None:
        self._save(schedule, "state", "schedule.json")

    def load_budget(self) -> dict:
        return self._load("state", "llm_budget.json")

    def save_budget(self, budget: dict) -> None:
        self._save(budget, "state", "llm_budget.json")

    def load_engine_health(self) -> dict:
        return self._load("state", "engine_health.json")

    def save_engine_health(self, health: dict) -> None:
        self._save(health, "state", "engine_health.json")

    def load_channel_state(self, channel_id: str) -> dict:
        return self._load("state", "deliveries", f"{channel_id}.json")

    def save_channel_state(self, channel_id: str, state: dict) -> None:
        self._save(state, "state", "deliveries", f"{channel_id}.json")

    def save_engine_run(self, run: dict) -> None:
        rid = run["run_id"]
        self._save(run, "runs", rid[:4], rid[4:6], f"{rid}-engine.json")

    # --- generic seen index -------------------------------------------------------------------------
    def load_seen_index(self, topic: str) -> dict:
        return self._load("state", "seen", f"{topic}.json")

    def save_seen_index(self, topic: str, seen: dict, now: datetime, keep_days: int = ITEMS_KEEP_DAYS) -> None:
        cutoff = (now - timedelta(days=keep_days)).isoformat()
        seen = {k: v for k, v in seen.items() if v.get("last_seen", "") >= cutoff}
        self._save(seen, "state", "seen", f"{topic}.json")

    # --- items (alert topics) ---------------------------------------------------------------------
    def save_items(self, topic: str, run_id: str, items: list[Item]) -> None:
        data = [i.model_dump(mode="json") for i in items]
        _write_json(self.root / "items" / topic / run_id[:4] / run_id[4:6] / f"{run_id}.json",
                    json.dumps(data, indent=1, ensure_ascii=False))

    def recent_items(self, topic: str, since: datetime) -> list[tuple[str, Item]]:
        """[(run_id, item)] newest first, from runs at/after `since`."""
        base = self.root / "items" / topic
        if not base.is_dir():
            return []
        since_id = since.strftime("%Y%m%dT%H%M%SZ")
        out = []
        for path in sorted(base.glob("*/*/*.json"), key=lambda p: p.stem, reverse=True):
            if path.stem < since_id:
                break
            for raw in json.loads(path.read_text(encoding="utf-8")):
                out.append((path.stem, Item.model_validate(raw)))
        return out

    def prune_items(self, topic: str, now: datetime, keep_days: int = ITEMS_KEEP_DAYS) -> int:
        base = self.root / "items" / topic
        cutoff = (now - timedelta(days=keep_days)).strftime("%Y%m%dT%H%M%SZ")
        removed = 0
        for path in base.glob("*/*/*.json") if base.is_dir() else []:
            if path.stem < cutoff:
                path.unlink()
                removed += 1
        return removed

    # --- generic digests ----------------------------------------------------------------------------
    def save_topic_digest(self, digest: TopicDigest) -> Path:
        path = self.digest_path(digest.topic, digest.run_id)
        _write_json(path, digest.model_dump_json(indent=1))
        return path

    def recent_topic_digests(self, topic: str, limit: int) -> list[TopicDigest]:
        base = self.root / "digests" / topic
        if not base.is_dir():
            return []
        files = sorted(base.glob("*/*/*.json"), key=lambda p: p.stem, reverse=True)[:limit]
        return [TopicDigest.model_validate_json(p.read_text(encoding="utf-8")) for p in files]
