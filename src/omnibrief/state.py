"""JSON state on the `data` branch (checked out at ./data). No database.

Layout:
  state/seen/{topic}.json                 key -> SeenEntry (only records that were emitted)
  state/sources/{topic}.json              source id -> last successful discovery (catch-up window)
  state/deliveries/{publisher}.json      push publishers: what was sent (idempotent retry)
  state/health.json                       last_success_utc carried between runs
  digests/{topic}/{yyyy}/{mm}/{runId}.json
  runs/{yyyy}/{mm}/{runId}.json
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from omnibrief.models import Digest, RunRecord


class SeenEntry(BaseModel):
    first_seen: datetime
    last_seen: datetime
    last_material_change: datetime
    aliases: list[str] = Field(default_factory=list)
    kev: bool = False
    severity: str | None = None
    patched: list[str] = Field(default_factory=list)


def _write_json(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(data + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)

    # --- seen index -------------------------------------------------------------------------
    def seen_path(self, topic: str) -> Path:
        return self.root / "state" / "seen" / f"{topic}.json"

    def load_seen(self, topic: str) -> dict[str, SeenEntry]:
        path = self.seen_path(topic)
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: SeenEntry.model_validate(v) for k, v in raw.items()}

    def save_seen(self, topic: str, seen: dict[str, SeenEntry]) -> None:
        data = {k: seen[k].model_dump(mode="json") for k in sorted(seen)}
        _write_json(self.seen_path(topic), json.dumps(data, indent=1, sort_keys=True))

    @staticmethod
    def prune_seen(seen: dict[str, SeenEntry], now: datetime, days: int = 90) -> int:
        cutoff = now - timedelta(days=days)
        stale = [k for k, v in seen.items() if v.last_seen < cutoff]
        for k in stale:
            del seen[k]
        return len(stale)

    # --- per-source watermarks (last successful discovery) ----------------------------------
    def _watermark_path(self, topic: str) -> Path:
        return self.root / "state" / "sources" / f"{topic}.json"

    def load_watermarks(self, topic: str) -> dict[str, datetime]:
        path = self._watermark_path(topic)
        if not path.is_file():
            return {}
        return {k: datetime.fromisoformat(v) for k, v in json.loads(path.read_text(encoding="utf-8")).items()}

    def save_watermarks(self, topic: str, marks: dict[str, datetime]) -> None:
        data = {k: v.isoformat() for k, v in sorted(marks.items())}
        _write_json(self._watermark_path(topic), json.dumps(data, indent=1))

    # --- push-publisher delivery records -------------------------------------------------------
    def _delivery_path(self, publisher_id: str) -> Path:
        return self.root / "state" / "deliveries" / f"{publisher_id}.json"

    def load_delivery(self, publisher_id: str) -> dict:
        path = self._delivery_path(publisher_id)
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def save_delivery(self, publisher_id: str, state: dict) -> None:
        _write_json(self._delivery_path(publisher_id), json.dumps(state, indent=1, sort_keys=True))

    # --- digests ----------------------------------------------------------------------------
    def digest_path(self, topic: str, run_id: str) -> Path:
        return self.root / "digests" / topic / run_id[:4] / run_id[4:6] / f"{run_id}.json"

    def save_digest(self, digest: Digest) -> Path:
        path = self.digest_path(digest.topic, digest.run_id)
        _write_json(path, digest.model_dump_json(indent=1))
        return path

    def recent_digests(self, topic: str, limit: int) -> list[Digest]:
        """Newest first. Run ids sort chronologically, so file names do too."""
        base = self.root / "digests" / topic
        if not base.is_dir():
            return []
        files = sorted(base.glob("*/*/*.json"), key=lambda p: p.stem, reverse=True)[:limit]
        return [Digest.model_validate_json(p.read_text(encoding="utf-8")) for p in files]

    # --- run records / health ---------------------------------------------------------------
    def save_run(self, run: RunRecord) -> Path:
        path = self.root / "runs" / run.run_id[:4] / run.run_id[4:6] / f"{run.run_id}.json"
        _write_json(path, run.model_dump_json(indent=1))
        return path

    def load_health_state(self) -> dict:
        path = self.root / "state" / "health.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def save_health_state(self, state: dict) -> None:
        _write_json(self.root / "state" / "health.json", json.dumps(state, indent=1, sort_keys=True))
