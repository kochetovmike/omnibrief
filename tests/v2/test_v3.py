"""design-v3 acceptance checks (Phase 0/2/3/4), offline."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from omnibrief.__main__ import main
from omnibrief.engine import runner
from omnibrief.engine.brief import load_project
from omnibrief.engine.config import ConfigError
from omnibrief.engine.expr import compile_expr, evaluate
from tests.v2.helpers import mock_sources

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path):
    for d in ("briefs", "prompts"):
        shutil.copytree(ROOT / d, tmp_path / d)
    shutil.copy(ROOT / "omnibrief.yaml", tmp_path / "omnibrief.yaml")
    return tmp_path


def edit(repo, brief, old, new):
    p = repo / "briefs" / f"{brief}.yaml"
    p.write_text(p.read_text(encoding="utf-8").replace(old, new), encoding="utf-8")


def test_all_seven_briefs_validate():
    assert len(load_project(ROOT).enabled_topics()) == 7


@pytest.mark.parametrize("brief,old,new,msg", [
    ("tenders", "connection: tg_bot", 'connection: tg_bot, token: "123456789:AAH' + "x" * 33 + '"', "credential-like"),
    ("earthquakes", "type: usgs_fdsn, licence: builtin", 'type: rss, licence: builtin', "needs a licence block"),
    ("earthquakes", "  - type: feed", "  - type: fax", "unknown output type"),
    ("earthquakes", 'title: "Earthquakes: Europe & Alps"\n', "", "title"),
    ("earthquakes", "fields.magnitude >= 4.0", "__import__('os')", "filters.expr"),
])
def test_check_rejects(repo, brief, old, new, msg):
    edit(repo, brief, old, new)
    with pytest.raises(ConfigError, match=msg):
        load_project(repo)


def test_safe_expr():
    e = compile_expr("fields.magnitude >= 4.5 or (fields.country == 'Austria' and fields.magnitude >= 3.0)")
    assert evaluate(e, {"fields": {"magnitude": 3.2, "country": "Austria"}})
    assert not evaluate(e, {"fields": {"magnitude": None}})
    for bad in ("fields.__class__.x", "open('x')", "().__class__", "os.system"):
        with pytest.raises(ValueError):
            compile_expr(bad)


def test_new_generic_brief_needs_no_code(repo):
    main(["--root", str(repo), "new-brief", "my-feed", "--type", "rss"])
    edit(repo, "my-feed", "enabled: false", "enabled: true")
    edit(repo, "my-feed", 'url: "https://example.org/feed"', 'url: "https://feeds.example/rss.xml"')
    rss = (b'<rss><channel><item><title>Hello</title><link>https://feeds.example/a</link><guid>a</guid>'
           b'<pubDate>Sat, 21 Nov 2026 11:00:00 GMT</pubDate></item></channel></rss>')
    project = load_project(repo)
    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        mock_sources(router)
        router.get("https://feeds.example/rss.xml").mock(return_value=httpx.Response(200, content=rss))
        rec = runner.run(project, runner.RunPaths(repo / "data", repo / "_site"), {}, now=NOW,
                         force={"my-feed"}, client=client, use_llm=False)
    assert rec.topics["my-feed"]["counts"]["emitted"] == 1
    assert "Hello" in (repo / "_site" / "feeds" / "my-feed.atom").read_text(encoding="utf-8")
    assert "my-feed" in (repo / "_site" / "index.html").read_text(encoding="utf-8")


def test_no_secrets_every_brief_publishes_feeds(repo):
    project = load_project(repo)
    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        mock_sources(router)
        router.route(url__regex=r"https://(www\.cisa\.gov|services\.nvd|api\.github|api\.first)").mock(
            return_value=httpx.Response(404))
        rec = runner.run(project, runner.RunPaths(repo / "data", repo / "_site"), {}, now=NOW, force={"all"},
                         client=client, use_llm=False)
        statuses = runner.deliver(project, repo / "data", {}, now=NOW, client=client)
    assert all(r["status"] in ("ok", "degraded") for t, r in rec.topics.items() if t != "vulns")
    for b in project.briefs:
        assert (repo / "_site" / "feeds" / f"{b}.atom").is_file()
    assert set(statuses.values()) == {"disabled"}  # no Telegram secrets → outputs off, not errors
    about = (repo / "_site" / "about.html").read_text(encoding="utf-8")
    assert "© European Union, TED" in about and "Datenquelle: GeoSphere Austria" in about


def test_each_brief_reaches_exactly_its_outputs(repo):
    project = load_project(repo)
    env = {"TELEGRAM_BOT_TOKEN": "1:x", "TG_ALERTS_CHAT_ID": "@alerts", "TG_TENDERS_CHAT_ID": "@tenders"}
    sent = []
    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        mock_sources(router)
        router.route(url__regex=r"https://(www\.cisa\.gov|services\.nvd|api\.github|api\.first)").mock(
            return_value=httpx.Response(404))
        runner.run(project, runner.RunPaths(repo / "data", repo / "_site"), {}, now=NOW,
                   force={"tenders", "weather-at"}, client=client, use_llm=False)
        router.post(url__startswith="https://api.telegram.org/").mock(side_effect=lambda r: (
            sent.append(r.content.decode()), httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}))[1])
        statuses = runner.deliver(project, repo / "data", env, now=NOW, client=client, sleep=lambda s: None)
    assert statuses["tg_alerts"] == "ok" and statuses["tg_security"] == "disabled"
    assert any('"@alerts"' in m and "storm warning" in m for m in sent)
    assert any('"@tenders"' in m and "TED" in m for m in sent)


def test_schema_and_notice_are_up_to_date():
    assert main(["--root", str(ROOT), "schema", "--check"]) == 0
    assert main(["--root", str(ROOT), "notice", "--check"]) == 0


def test_vulns_prompt_file_resolves_from_repo_root():
    """Regression: v1 code joins prompt_file onto the package catalogue; it must be absolute."""
    from omnibrief.engine.topics.vulns import build_v1_config

    project = load_project(ROOT)
    cfg = build_v1_config(project, project.topics["vulns"])
    assert (cfg.root / cfg.topic("vulns").prompt_file).is_file()
