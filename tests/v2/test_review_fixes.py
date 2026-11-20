"""Regression tests for the independent review of the v2 engine (findings #1, #5–#8, #10–#12)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from omnibrief.engine.channels import TelegramChannel
from omnibrief.engine.config import ChannelSettings, ConfigError, SourceSpec
from omnibrief.engine.items import Outbound
from omnibrief.engine.render import clip_telegram, html_to_telegram
from omnibrief.engine.sources import SourceCtx
from omnibrief.engine.sources.arxiv import URL as ARXIV
from omnibrief.engine.sources.arxiv import ArxivSource
from omnibrief.engine.sources.ted import URL as TED
from omnibrief.engine.sources.ted import TedSource
from omnibrief.render import sanitise
from tests.v2.helpers import engine_for

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


# --- #1 Telegram HTML -------------------------------------------------------------------------------

def test_telegram_html_uses_only_allowed_entities_and_tags():
    html = sanitise("**Buyer GmbH** & <x> [dropped](https://evil.example) [ok](https://a.example/?a=1&b=2)",
                    {"https://a.example/?a=1&b=2"})
    tg = html_to_telegram("<h3><strong>Title</strong></h3>" + html)
    assert "&nbsp;" not in tg and " " in tg
    assert "<b><b>" not in tg and "<a rel" not in tg and "evil.example" not in tg
    assert '<a href="https://a.example/?a=1&amp;b=2">' in tg and "&amp;" in tg


def test_clip_closes_tags_and_never_cuts_inside_tags_or_entities():
    text = "<b>bold " + "x " * 50 + '<a href="https://a.example">link\ncontinues</a></b> tail &amp; more'
    for limit in range(60, 200, 7):
        out = clip_telegram(text, limit=limit, more="…")
        body = out[: -len("\n…")]
        assert body.count("<b>") == body.count("</b>") and body.count("<a ") == body.count("</a>")
        assert not body.rstrip().endswith("&") and "&am" not in body.replace("&amp;", "")


def test_one_bad_message_does_not_block_the_channel():
    env = {"TELEGRAM_BOT_TOKEN": "1:x", "C": "@c"}
    ch = TelegramChannel("tg", ChannelSettings(type="telegram", chat_id_env="C"), env, "https://x/", "Europe/Vienna")
    outs = [Outbound(topic="t", key=f"k{i}", text=f"m{i}", created_utc=NOW + timedelta(seconds=i)) for i in range(3)]
    responses = iter([httpx.Response(400, json={"ok": False, "description": "Bad Request: can't parse entities"}),
                      httpx.Response(200, json={"ok": True, "result": {"message_id": 2}}),
                      httpx.Response(200, json={"ok": True, "result": {"message_id": 3}})])
    with respx.mock() as router, httpx.Client() as client:
        router.post(url__startswith="https://api.telegram.org/").mock(side_effect=lambda r: next(responses))
        state = ch.deliver(outs, {}, client, NOW, sleep=lambda s: None)
    assert state["status"] == "degraded"
    assert "failed" in state["sent"]["t:k0"] and state["sent"]["t:k2"]["message_id"] == 3


# --- #5 arXiv / #6 TED pagination -------------------------------------------------------------------

def _arxiv_page(start, n, newest):
    entries = "".join(
        f"<entry><id>http://arxiv.org/abs/2611.{start + i:05d}v1</id><title>T{start + i}</title>"
        f"<summary>s</summary><published>{(newest - timedelta(hours=start + i)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
        f"</published></entry>" for i in range(n))
    return f'<feed xmlns="http://www.w3.org/2005/Atom">{entries}</feed>'.encode()


def test_arxiv_pages_until_older_than_window():
    sleeps = []
    with respx.mock() as router, httpx.Client() as client:
        route = router.get(url__startswith=ARXIV).mock(side_effect=lambda r: httpx.Response(
            200, content=_arxiv_page(int(r.url.params["start"]), 3, NOW)))
        src = ArxivSource(SourceSpec(id="a", type="arxiv", params={"page_size": 3, "max_total": 30}),
                          SourceCtx(client=client, sleep=sleeps.append))
        items = src.fetch("t", NOW - timedelta(hours=7, minutes=30), NOW)
        calls = route.call_count
    assert len(items) == 8 and calls == 3 and sleeps == [3.0, 3.0]


def test_ted_sorts_newest_first_paginates_and_flags_truncation():
    def answer(request):
        body = json.loads(request.content)
        page = body["page"]
        return httpx.Response(200, json={"totalNoticeCount": 5, "notices": [
            {"publication-number": f"{page}{i}-2026", "notice-title": {"eng": "x"},
             "publication-date": "2026-11-20+01:00"} for i in range(2)]})

    with respx.mock() as router, httpx.Client() as client:
        route = router.post(TED).mock(side_effect=answer)
        src = TedSource(SourceSpec(id="t", type="ted", params={"limit": 2, "max_pages": 2}),
                        SourceCtx(client=client, sleep=lambda s: None))
        items = src.fetch("t", NOW - timedelta(days=1), NOW)
        bodies = [json.loads(c.request.content) for c in route.calls]
    assert [b["page"] for b in bodies] == [1, 2] and "SORT BY publication-number DESC" in bodies[0]["query"]
    assert len(items) == 4 and src.truncated


# --- #7 credits ----------------------------------------------------------------------------------------

def test_cc_by_credit_in_digest_feed_rights_and_telegram(tmp_path):
    from omnibrief.engine import runner
    from tests.v2.helpers import mock_sources

    engine = engine_for(tmp_path, topics={"tenders": {"publish_to": ["feed"]}})
    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        mock_sources(router)
        runner.run(engine, runner.RunPaths(tmp_path / "data", tmp_path / "site"), {}, now=NOW, force={"all"},
                   client=client, use_llm=False)
    feed = (tmp_path / "site" / "feeds" / "tenders.atom").read_text(encoding="utf-8")
    assert "© European Union, TED (CC BY 4.0, modified)" in feed  # <rights>
    assert "© European Union, TED" in feed.split("<entry>")[1] and "creativecommons.org/licenses/by/4.0" in feed
    from omnibrief.engine.store import EngineStore
    from omnibrief.engine.topics import TopicContext, plugin_for

    spec = engine.topics["tenders"]
    ctx = TopicContext(engine=engine, spec=spec, store=EngineStore(tmp_path / "data"), client=None, env={}, now=NOW,
                       run_id="x", renderer=runner.Renderer(engine), site_dir=tmp_path)
    [msg] = plugin_for(spec).outbound(ctx, "tg", ChannelSettings(type="telegram"))
    assert len(msg.text) <= 4096 and "© European Union, TED" in msg.text.rsplit("\n", 2)[-2]


# --- #8 / #12 config validation ----------------------------------------------------------------------

def test_bad_not_before_and_quiet_hours_rejected_at_load(tmp_path):
    with pytest.raises(ConfigError) as e:
        engine_for(tmp_path, topics={"tenders": {"publish_to": ["feed", "tg"]}},
                   channels={"feed": {"type": "feed"}, "tg": {"type": "telegram", "quiet_hours": "late"}},
                   files={"topics/tenders.yaml": "cadence: { not_before: '9.30' }\n"})
    assert "not_before must be HH:MM" in str(e.value) and "quiet_hours must look like" in str(e.value)


def test_tokens_in_instance_text_files_rejected(tmp_path):
    with pytest.raises(ConfigError, match="credential-like value in prompts"):
        engine_for(tmp_path, files={"prompts/tenders.md": "use key AIza" + "B" * 35 + " {records}"})


def test_unknown_topic_reports_error_instead_of_traceback(tmp_path):
    with pytest.raises(ConfigError, match="not in the catalogue"):
        engine_for(tmp_path, topics={"nope": {"publish_to": ["feed"]}})
