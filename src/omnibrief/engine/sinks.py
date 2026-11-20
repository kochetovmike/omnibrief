"""Archive sinks (design-v2 §7): write-only replicas, written after the primary store succeeded.

  artifact   raw items + digests per run under ./out/raw (the workflow uploads it as an artifact)
  jsonl_git  append-only monthly JSONL per topic in a checkout of the `archive` branch
             (the workflow commits it). DuckDB can read it directly. Idempotent per (run, key).

A sink failure marks the sink `failed` in the run record and never fails the run.
"""

from __future__ import annotations

import json
from pathlib import Path

from omnibrief.engine.config import SinkSettings
from omnibrief.engine.items import Item, TopicDigest


class Sink:
    type = ""

    def __init__(self, settings: SinkSettings, out_dir: Path | None, archive_dir: Path | None):
        self.settings = settings
        self.out_dir = out_dir
        self.archive_dir = archive_dir

    @property
    def id(self) -> str:
        return self.type

    def is_enabled(self) -> tuple[bool, str]:
        return True, "on"

    def write(self, topic: str, run_id: str, items: list[Item], digest: TopicDigest | None) -> int:
        raise NotImplementedError


class ArtifactSink(Sink):
    type = "artifact"

    def is_enabled(self) -> tuple[bool, str]:
        return (self.out_dir is not None, "on" if self.out_dir else "no --out directory")

    def write(self, topic: str, run_id: str, items: list[Item], digest: TopicDigest | None) -> int:
        path = self.out_dir / "raw" / topic / f"{run_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"run_id": run_id, "items": [i.model_dump(mode="json") for i in items],
                   "digest": digest.model_dump(mode="json") if digest else None}
        path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        return len(items)


class JsonlGitSink(Sink):
    type = "jsonl_git"

    def is_enabled(self) -> tuple[bool, str]:
        return (self.archive_dir is not None, "on" if self.archive_dir else "no --archive checkout")

    def write(self, topic: str, run_id: str, items: list[Item], digest: TopicDigest | None) -> int:
        path = self.archive_dir / topic / f"{run_id[:4]}-{run_id[4:6]}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = set()
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                    existing.add((rec.get("run_id"), rec.get("key")))
                except ValueError:
                    continue
        new = [i for i in items if (run_id, i.key) not in existing]
        with path.open("a", encoding="utf-8", newline="\n") as fh:
            for i in new:
                fh.write(json.dumps({"run_id": run_id, **i.model_dump(mode="json")}, ensure_ascii=False) + "\n")
        return len(new)


SINK_TYPES = {cls.type: cls for cls in (ArtifactSink, JsonlGitSink)}


def build_sinks(settings: list[SinkSettings], out_dir: Path | None, archive_dir: Path | None) -> list[Sink]:
    return [SINK_TYPES[s.type](s, out_dir, archive_dir) for s in settings if s.type in SINK_TYPES]
