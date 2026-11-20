"""Source adapters for generic topics. Each turns one API into normalised Items.

Adapters raise on errors; the topic runner isolates each source (a failure degrades only that
source/topic). Adding a source = an adapter here + its licence in attribution.SOURCE_LICENCES +
NOTICE.md text + offline tests against a saved fixture.
"""

from __future__ import annotations

from omnibrief.engine.sources.arxiv import ArxivSource
from omnibrief.engine.sources.base import SourceAdapter, SourceCtx
from omnibrief.engine.sources.geosphere import GeoSphereWarnSource
from omnibrief.engine.sources.safety_gate import SafetyGateSource
from omnibrief.engine.sources.swpc import SwpcSource
from omnibrief.engine.sources.ted import TedSource
from omnibrief.engine.sources.usgs import UsgsSource

SOURCE_TYPES: dict[str, type[SourceAdapter]] = {
    cls.type: cls for cls in (UsgsSource, SwpcSource, GeoSphereWarnSource, ArxivSource, TedSource, SafetyGateSource)
}

__all__ = ["SOURCE_TYPES", "SourceAdapter", "SourceCtx"]
