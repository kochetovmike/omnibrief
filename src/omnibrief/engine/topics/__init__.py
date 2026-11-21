"""Topic plugins: `generic` (YAML-driven) and `vulns` (v1 pipeline: merge, Global Critical, dashboards)."""

from __future__ import annotations

from omnibrief.engine.topics.base import TopicContext, TopicPlugin, TopicResult
from omnibrief.engine.topics.generic import GenericTopic
from omnibrief.engine.topics.vulns import VulnsTopic

PLUGINS: dict[str, type[TopicPlugin]] = {"generic": GenericTopic, "vulns": VulnsTopic}


def plugin_for(spec) -> TopicPlugin:
    return PLUGINS[spec.plugin](spec)


__all__ = ["PLUGINS", "TopicContext", "TopicPlugin", "TopicResult", "plugin_for"]
