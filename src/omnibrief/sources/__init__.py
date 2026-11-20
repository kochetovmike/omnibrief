"""Source adapter registry. Adding a source = an adapter here + a sources.yaml entry,
after the licence is added to attribution.SOURCE_LICENCES (design §5)."""

from __future__ import annotations

from omnibrief.config import SourceConfig
from omnibrief.sources.base import Source, SourceContext, Window
from omnibrief.sources.cisa_kev import CisaKevSource
from omnibrief.sources.epss import EpssSource
from omnibrief.sources.ghsa import GhsaSource
from omnibrief.sources.nvd import NvdSource

SOURCE_TYPES: dict[str, type[Source]] = {
    cls.type: cls for cls in (CisaKevSource, NvdSource, GhsaSource, EpssSource)
}


def build_source(cfg: SourceConfig, ctx: SourceContext) -> Source:
    return SOURCE_TYPES[cfg.type](cfg, ctx)


__all__ = ["SOURCE_TYPES", "Source", "SourceContext", "Window", "build_source"]
