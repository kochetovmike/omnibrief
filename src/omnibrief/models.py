"""Core data contracts: Vuln (merged record), Digest (channel-neutral output), RunRecord."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "moderate": 2, "high": 3, "critical": 4}


def severity_rank(severity: str | None) -> int:
    return SEVERITY_RANK.get((severity or "").lower(), -1)


def severity_from_cvss(score: float | None) -> str | None:
    if score is None:
        return None
    if score >= 9.0:
        return "critical"
    if score >= 7.0:
        return "high"
    if score >= 4.0:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Package(BaseModel):
    ecosystem: str
    name: str
    vulnerable_range: str | None = None
    patched: str | None = None


class Vuln(BaseModel):
    """One vulnerability, possibly partial (straight from one source) or merged across sources.

    `id` is the canonical key: the CVE id if known, otherwise the GHSA id.
    `sources` holds the source *types* (cisa_kev, nvd, ghsa, epss) that contributed data;
    it drives attribution.
    """

    id: str
    aliases: list[str] = Field(default_factory=list)
    title: str = ""
    description: str = ""
    products: list[str] = Field(default_factory=list)
    packages: list[Package] = Field(default_factory=list)
    cvss: float | None = None
    cvss_vector: str | None = None
    severity: str | None = None
    kev: bool = False
    kev_date_added: str | None = None
    kev_ransomware: bool = False
    kev_required_action: str | None = None
    eu_exploited: bool = False
    epss: float | None = None
    epss_percentile: float | None = None
    published: datetime | None = None
    modified: datetime | None = None
    refs: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    # Set by the pipeline after dedupe:
    status: Literal["new", "updated"] = "new"
    changes: list[str] = Field(default_factory=list)

    def all_ids(self) -> list[str]:
        return [self.id, *[a for a in self.aliases if a != self.id]]

    def patched_versions(self) -> list[str]:
        return sorted({f"{p.ecosystem}:{p.name}@{p.patched}" for p in self.packages if p.patched})

    def advisory_page(self) -> str | None:
        """The NVD or GitHub advisory page for this record, if we know it."""
        for ref in self.refs:
            if ref.startswith(("https://nvd.nist.gov/vuln/detail/", "https://github.com/advisories/")):
                return ref
        return None


class DigestItem(BaseModel):
    id: str
    aliases: list[str] = Field(default_factory=list)
    title: str = ""
    product: str = ""
    severity: str | None = None
    cvss: float | None = None
    kev: bool = False
    epss_percentile: float | None = None
    status: Literal["new", "updated"] = "new"
    changes: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    summarised: bool = True  # False for overflow items listed as links only
    published: datetime | None = None  # when the vulnerability was published (NVD/GHSA)
    kev_date_added: str | None = None  # YYYY-MM-DD, when CISA flagged it as exploited


class Digest(BaseModel):
    """Channel-neutral digest. Publishers render it; they never call the LLM."""

    topic: str
    run_id: str
    created_utc: datetime
    title: str
    summarised: bool  # False => fallback (LLM unavailable), labelled as un-summarised
    markdown: str
    html: str  # sanitised
    items: list[DigestItem]
    sources: list[str] = Field(default_factory=list)
    # publisher id -> delivery record; empty means pending for push publishers
    deliveries: dict[str, dict] = Field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "exploited": sum(1 for i in self.items if i.kev),
            "critical": sum(1 for i in self.items if (i.severity or "").lower() == "critical"),
            "high": sum(1 for i in self.items if (i.severity or "").lower() == "high"),
            "total": len(self.items),
        }


class SourceStatus(BaseModel):
    status: Literal["ok", "failed"] = "ok"
    count: int = 0
    error: str | None = None


class PublisherStatus(BaseModel):
    status: Literal["ok", "disabled", "failed", "pending"] = "ok"
    reason: str | None = None


class RunRecord(BaseModel):
    run_id: str
    topic: str
    started_utc: datetime
    finished_utc: datetime | None = None
    status: Literal["ok", "degraded", "failed"] = "ok"
    sources: dict[str, SourceStatus] = Field(default_factory=dict)
    publishers: dict[str, PublisherStatus] = Field(default_factory=dict)
    llm: Literal["ok", "fallback", "skipped", "not_configured"] = "skipped"
    counts: dict[str, int] = Field(default_factory=dict)
    digest_written: bool = False


def make_run_id(now: datetime) -> str:
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
