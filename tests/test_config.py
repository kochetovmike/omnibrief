from __future__ import annotations

import pytest
import yaml

from omnibrief.config import ConfigError, check_notice, load_config


def _edit(repo, mutate):
    path = repo / "sources.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_shipped_config_is_valid(cfg):
    assert cfg.topic("vulns").source_types() == ["cisa_kev", "epss", "ghsa", "nvd"]
    assert cfg.site.base.endswith("/")
    assert check_notice(cfg) == []


def test_rejects_source_not_on_allowlist(repo):
    path = _edit(repo, lambda d: d["topics"][0]["discovery"].append(
        {"id": "news", "type": "security_news", "url": "https://example.com"}))
    with pytest.raises(ConfigError, match="allowlist"):
        load_config(path)


def test_rejects_allowlisted_source_without_adapter_when_enabled(repo):
    path = _edit(repo, lambda d: d["topics"][0]["discovery"].append(
        {"id": "euvd", "type": "euvd", "url": "https://euvdservices.enisa.europa.eu"}))
    with pytest.raises(ConfigError, match="no adapter"):
        load_config(path)


def test_allows_disabled_allowlisted_source_without_adapter(repo):
    path = _edit(repo, lambda d: d["topics"][0]["discovery"].append(
        {"id": "euvd", "type": "euvd", "url": "https://x", "enabled": False}))
    load_config(path)


def test_rejects_duplicate_source_ids(repo):
    path = _edit(repo, lambda d: d["topics"][0]["discovery"].append(
        {"id": "nvd", "type": "nvd", "url": "https://x"}))
    with pytest.raises(ConfigError, match="duplicate source ids"):
        load_config(path)


def test_rejects_unknown_publisher_type(repo):
    path = _edit(repo, lambda d: d["publishers"].update({"fax": {"type": "fax"}}))
    with pytest.raises(ConfigError, match="unknown type"):
        load_config(path)


def test_rejects_missing_prompt_file(repo):
    (repo / "prompts" / "vulns.md").unlink()
    with pytest.raises(ConfigError, match="prompt file"):
        load_config(repo / "sources.yaml")


def test_rejects_missing_base_url(repo):
    path = _edit(repo, lambda d: d["site"].pop("base_url"))
    with pytest.raises(ConfigError, match="base_url"):
        load_config(path)


def test_notice_check_flags_missing_attribution(repo, cfg):
    (repo / "NOTICE.md").write_text("# nothing here\n", encoding="utf-8")
    missing = check_notice(cfg)
    assert any("nvd" in m for m in missing)
    assert any("ghsa" in m for m in missing)
