"""End-to-end engine runs (offline): design-v2 Phase 1–3 acceptance criteria."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import httpx
import respx

from omnibrief.engine import runner
from omnibrief.engine.store import EngineStore
from tests.v2.helpers import GENERIC, engine_for, mock_sources

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)  # Saturday 13:00 CET
ATOM = "{http://www.w3.org/2005/Atom}"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent"


def go(tmp_path, engine, now=NOW, force=None, env=None, fail=None, archive=True, use_llm=True, gemini=None):
    paths = runner.RunPaths(data_dir=tmp_path / "data", site_dir=tmp_path / "site", out_dir=tmp_path / "out",
                            archive_dir=(tmp_path / "archive") if archive else None)
    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        mock_sources(router, fail=fail)
        if gemini is not None:
            router.post(GEMINI).mock(side_effect=gemini)
        rec = runner.run(engine, paths, env or {}, now=now, force={"all"} if force is None else force, client=client,
                         use_llm=use_llm)
        calls = [c.request for c in router.calls]
    return rec, paths, calls


def test_forced_run_of_all_topics_with_no_channels_and_no_llm(tmp_path):
    engine = engine_for(tmp_path)
    rec, paths, _ = go(tmp_path, engine)
    assert set(rec.topics) == set(GENERIC)
    assert all(r["status"] in ("ok", "degraded") for r in rec.topics.values()), rec.topics
    site = paths.site_dir
    for t in GENERIC:  # Atom + RSS per topic, topic page
        assert ET.parse(site / "feeds" / f"{t}.atom").getroot().tag == f"{ATOM}feed"
        assert ET.parse(site / "feeds" / f"{t}.rss").getroot().tag == "rss"
        assert (site / "topics" / f"{t}.html").is_file()
    for f in ("index.html", "status.html", "about.html", "health.json", "style.css", "favicon.svg",
              "feeds/all.atom", "feeds/all.rss", "feeds/omnibrief.opml"):
        assert (site / f).is_file(), f
    # digests are published as un-summarised fallbacks without an LLM key
    tenders = ET.parse(site / "feeds" / "tenders.atom").getroot().find(f"{ATOM}entry")
    assert "un-summarised" in tenders.findtext(f"{ATOM}title")
    opml = (site / "feeds" / "omnibrief.opml").read_text(encoding="utf-8")
    assert "feeds/earthquakes.atom" in opml and "feeds/all.atom" in opml
    health = json.loads((site / "health.json").read_text())
    assert set(health["topics"]) == set(GENERIC) and health["channels"]["feed"]["status"] == "ok"


def test_two_consecutive_runs_zero_duplicates_and_material_change_reemits(tmp_path):
    engine = engine_for(tmp_path, topics={"weather_at": {"publish_to": ["feed"]}})
    rec1, paths, _ = go(tmp_path, engine)
    assert rec1.topics["weather_at"]["counts"]["emitted"] == 1  # orange storm; the yellow one is filtered
    rec2, _, _ = go(tmp_path, engine, now=NOW + timedelta(hours=1))
    assert rec2.topics["weather_at"]["counts"]["emitted"] == 0
    # GeoSphere raises the warning to red -> re-emitted as "updated"
    store = EngineStore(paths.data_dir)
    seen = store.load_seen_index("weather_at")
    seen["geosphere:w4147c0v2"]["material_hash"] = "stale"
    store.save_seen_index("weather_at", seen, NOW)
    rec3, _, _ = go(tmp_path, engine, now=NOW + timedelta(hours=2))
    assert rec3.topics["weather_at"]["counts"]["emitted"] == 1
    items = store.recent_items("weather_at", NOW + timedelta(hours=1))
    assert [i.status for _, i in items] == ["updated"]
    entries = ET.parse(paths.site_dir / "feeds" / "weather_at.atom").getroot().findall(f"{ATOM}entry")
    assert len(entries) == 2 and entries[0].findtext(f"{ATOM}title").startswith("Updated · ")


def test_one_source_down_degrades_only_its_topic(tmp_path):
    engine = engine_for(tmp_path)
    rec, paths, _ = go(tmp_path, engine, fail={"ted"})
    assert rec.topics["tenders"]["status"] == "failed"
    assert all(rec.topics[t]["status"] != "failed" for t in GENERIC if t != "tenders")
    assert rec.status == "degraded"
    sched = EngineStore(paths.data_dir).load_schedule()
    assert "last_success" not in sched["tenders"] and "last_success" in sched["arxiv_ai"]  # retried next run


def test_sink_failure_never_fails_the_run(tmp_path, monkeypatch):
    from omnibrief.engine import sinks

    def boom(self, *a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(sinks.JsonlGitSink, "write", boom)
    engine = engine_for(tmp_path)
    rec, paths, _ = go(tmp_path, engine)
    assert rec.sinks["jsonl_git"].startswith("failed") and rec.sinks["artifact"] == "ok"
    assert all(r["status"] != "failed" for r in rec.topics.values())
    assert (paths.site_dir / "index.html").is_file()


def test_sinks_write_and_are_idempotent(tmp_path):
    engine = engine_for(tmp_path, topics={"safety_gate": {"publish_to": ["feed"]}})
    rec, paths, _ = go(tmp_path, engine)
    jsonl = next((tmp_path / "archive" / "safety_gate").glob("*.jsonl"))
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 6 and all(json.loads(x)["run_id"] == rec.run_id for x in lines)
    from omnibrief.engine.sinks import JsonlGitSink
    from omnibrief.engine.store import EngineStore as S

    items = S(paths.data_dir).recent_topic_digests("safety_gate", 1)[0].items
    assert JsonlGitSink(None, None, tmp_path / "archive").write("safety_gate", rec.run_id, items, None) == 0
    assert (paths.out_dir / "raw" / "safety_gate" / f"{rec.run_id}.json").is_file()


def test_llm_budget_exhaustion_falls_back_cleanly(tmp_path):
    engine = engine_for(tmp_path, llm={"model": "m", "max_calls_per_run": 1, "max_calls_per_day": 40},
                        topics={t: {"publish_to": ["feed"]} for t in ("tenders", "safety_gate", "arxiv_ai")})
    ok = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "- summary"}]}}]})
    rec, paths, calls = go(tmp_path, engine, env={"GEMINI_API_KEY": "k"}, gemini=lambda req: ok)
    assert sum(1 for c in calls if "generativelanguage" in str(c.url)) == 1
    llm = sorted(r["llm"] for r in rec.topics.values())
    assert llm == ["budget", "budget", "ok"]
    assert EngineStore(paths.data_dir).load_budget()["calls"] == 1
    health = json.loads((paths.site_dir / "health.json").read_text())
    assert health["llm"]["calls_today"] == 1


def test_daily_cap_carries_across_runs(tmp_path):
    engine = engine_for(tmp_path, llm={"model": "m", "max_calls_per_run": 5, "max_calls_per_day": 1},
                        topics={"tenders": {"publish_to": ["feed"]}})
    ok = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "- s"}]}}]})
    go(tmp_path, engine, env={"GEMINI_API_KEY": "k"}, gemini=lambda req: ok)
    EngineStore(tmp_path / "data").save_seen_index("tenders", {}, NOW)  # make items new again
    rec, _, calls = go(tmp_path, engine, now=NOW + timedelta(hours=1), env={"GEMINI_API_KEY": "k"},
                       gemini=lambda req: ok)
    assert rec.topics["tenders"]["llm"] == "budget" and not any("generativelanguage" in str(c.url) for c in calls)


def test_planner_skips_topics_not_due(tmp_path):
    engine = engine_for(tmp_path)
    go(tmp_path, engine)
    rec, _, calls = go(tmp_path, engine, now=NOW + timedelta(minutes=30), force=set())
    assert rec.topics == {}  # nothing due 30 minutes later
    assert not calls
    assert rec.due["tenders"].startswith("skipped")


def test_untrusted_text_cannot_inject_into_pages_or_feeds(tmp_path, monkeypatch):
    from omnibrief.engine.sources import usgs

    real = usgs.parse_feature

    def evil(topic_id, f):
        it = real(topic_id, f)
        if it:
            it.fields["place"] = '<script>alert(1)</script><img src=x onerror=alert(2)>'
            it.title = "<b>M9.9</b> <script>x()</script>"
            it.fields["magnitude"] = 9.9
        return it

    monkeypatch.setattr(usgs, "parse_feature", evil)
    engine = engine_for(tmp_path, topics={"earthquakes": {"publish_to": ["feed"]}})
    _, paths, _ = go(tmp_path, engine)
    for f in ("topics/earthquakes.html", "index.html"):
        html = (paths.site_dir / f).read_text(encoding="utf-8")
        assert "<script>" not in html and "<img src=x" not in html
    content = ET.parse(paths.site_dir / "feeds" / "earthquakes.atom").getroot().find(f"{ATOM}entry").findtext(
        f"{ATOM}content")
    assert "<script>" not in content and "&lt;script&gt;" in content


def test_render_only_rebuilds_site_without_fetching(tmp_path):
    engine = engine_for(tmp_path)
    _, paths, _ = go(tmp_path, engine)
    for p in paths.site_dir.rglob("*"):
        if p.is_file():
            p.unlink()
    with respx.mock() as router:
        runner.run(engine, runner.RunPaths(data_dir=paths.data_dir, site_dir=paths.site_dir), {}, now=NOW,
                   collect=False, use_llm=False)
        assert not router.calls
    assert (paths.site_dir / "feeds" / "tenders.atom").is_file() and (paths.site_dir / "index.html").is_file()
