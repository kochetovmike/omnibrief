"""Builds the list of publishers for a topic: topics[].publishers × publishers: × is_enabled().

Disabled publishers are logged once and recorded as `disabled` — that is not a degraded run.
Adding a channel = a Publisher subclass registered here + a `publishers:` entry in sources.yaml.
"""

from __future__ import annotations

import logging

from omnibrief.models import PublisherStatus
from omnibrief.publishers.atom import AtomPublisher
from omnibrief.publishers.base import Publisher, PublisherContext
from omnibrief.publishers.telegram import TelegramPublisher

log = logging.getLogger(__name__)

PUBLISHER_TYPES: dict[str, type[Publisher]] = {cls.type: cls for cls in (AtomPublisher, TelegramPublisher)}


def build_publishers(ctx: PublisherContext) -> tuple[list[Publisher], dict[str, PublisherStatus]]:
    enabled: list[Publisher] = []
    statuses: dict[str, PublisherStatus] = {}
    for pid in ctx.topic.publishers:
        pcfg = ctx.cfg.publishers[pid]
        pub = PUBLISHER_TYPES[pcfg.type](pid, pcfg, ctx)
        try:
            ok, reason = pub.is_enabled()
        except Exception as e:  # is_enabled must not raise; treat a bug as disabled, not fatal
            ok, reason = False, f"is_enabled error: {type(e).__name__}"
        if ok:
            enabled.append(pub)
        else:
            log.info("%s: disabled (%s)", pid, reason)
            statuses[pid] = PublisherStatus(status="disabled", reason=reason)
    return enabled, statuses
