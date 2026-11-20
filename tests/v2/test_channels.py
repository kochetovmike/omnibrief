"""Push channels under v2: per-topic routing, caps, quiet hours, idempotency, v1 migration."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import respx

from omnibrief.engine import runner
from omnibrief.engine.channels import in_quiet_hours
from omnibrief.engine.store import EngineStore
from omnibrief.models import Digest, DigestItem
from tests.v2.helpers import engine_for, mock_sources

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)  # 13:00 CET
TOKEN = "123456:TEST-token-not-real"
SEND = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TG_ALERTS": "@alerts", "TG_TENDERS": "@tenders", "TG_SEC": "@security"}
CHANNELS = {
    "feed": {"type": "feed"},
    "tg_alerts": {"type": "telegram", "bot_token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TG_ALERTS",
                  "max_posts_per_run": 10, "quiet_hours": "23:00-06:00"},
    "tg_tenders": {"type": "telegram", "bot_token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TG_TENDERS"},
}
TOPICS = {"weather_at": {"publish_to": ["feed", "tg_alerts"]}, "safety_gate": {"publish_to": ["feed", "tg_alerts"]},
          "tenders": {"publish_to": ["feed", "tg_tenders"]}, "arxiv_ai": {"publish_to": ["feed"]}}


def collect(tmp_path, engine, now=NOW):
    paths = runner.RunPaths(data_dir=tmp_path / "data", site_dir=tmp_path / "site")
    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        mock_sources(router)
        runner.run(engine, paths, {}, now=now, force={"all"}, client=client, use_llm=False)
    return paths


def deliver(tmp_path, engine, now=NOW, env=ENV, test=False, response=None):
    sent = []

    def answer(request):
        body = json.loads(request.content)
        sent.append(body)
        return response or httpx.Response(200, json={"ok": True, "result": {"message_id": len(sent)}})

    with respx.mock(assert_all_called=False) as router, httpx.Client() as client:
        router.post(SEND).mock(side_effect=answer)
        statuses = runner.deliver(engine, tmp_path / "data", env, now=now, client=client, test=test,
                                  sleep=lambda s: None)
    return statuses, sent


def test_each_topic_reaches_exactly_its_channels_once(tmp_path):
    engine = engine_for(tmp_path, topics=TOPICS, channels=CHANNELS)
    collect(tmp_path, engine)
    statuses, sent = deliver(tmp_path, engine)
    assert statuses == {"tg_alerts": "ok", "tg_tenders": "ok"}
    by_chat = {}
    for m in sent:
        by_chat.setdefault(m["chat_id"], []).append(m["text"])
    assert set(by_chat) == {"@alerts", "@tenders"}
    alerts = by_chat["@alerts"]
    assert any("Orange storm warning" in t for t in alerts)  # weather_at alert, rendered from template
    assert any("Safety Gate" in t or "safety" in t.lower() for t in alerts)  # safety_gate digest
    assert not any("Public tenders" in t for t in alerts)
    assert all("Public tenders" in t for t in by_chat["@tenders"])
    assert all(m["parse_mode"] == "HTML" and m["link_preview_options"] == {"is_disabled": True} for m in sent)
    # arXiv is feed-only: nothing posted for it anywhere
    assert not any("arXiv" in t for ts in by_chat.values() for t in ts)
    _, again = deliver(tmp_path, engine, now=NOW + timedelta(minutes=5))
    assert again == []  # idempotent


def test_channels_without_settings_are_disabled_not_failed(tmp_path):
    engine = engine_for(tmp_path, topics=TOPICS, channels=CHANNELS)
    collect(tmp_path, engine)
    statuses, sent = deliver(tmp_path, engine, env={})
    assert statuses == {"tg_alerts": "disabled", "tg_tenders": "disabled"} and sent == []
    assert EngineStore(tmp_path / "data").load_channel_state("tg_alerts")["reason"] == "TELEGRAM_BOT_TOKEN not set"


def test_max_posts_per_run_overflow_becomes_one_post(tmp_path):
    channels = {**CHANNELS, "tg_alerts": {**CHANNELS["tg_alerts"], "max_posts_per_run": 2}}
    topics = {"earthquakes": {"publish_to": ["feed", "tg_alerts"]}}
    engine = engine_for(tmp_path, topics=topics, channels=channels,  # every quake in the fixture passes
                        files={"topics/earthquakes.yaml": "filters: { min_fields: { magnitude: 0 } }\n"})
    collect(tmp_path, engine)
    _, sent = deliver(tmp_path, engine)
    assert len(sent) == 2 and sent[-1]["text"].startswith("➕ <b>")
    state = EngineStore(tmp_path / "data").load_channel_state("tg_alerts")
    assert len(state["sent"]) == 3  # 1 posted + 2 summarised by the overflow post
    _, again = deliver(tmp_path, engine, now=NOW + timedelta(minutes=5))
    assert again == []


def test_quiet_hours_hold_non_critical(tmp_path):
    assert in_quiet_hours("23:00-06:00", datetime(2026, 11, 21, 23, 30, tzinfo=UTC), "Europe/Vienna")  # 00:30 CET
    assert not in_quiet_hours("23:00-06:00", NOW, "Europe/Vienna")
    engine = engine_for(tmp_path, topics={"weather_at": {"publish_to": ["feed", "tg_alerts"]}}, channels=CHANNELS)
    collect(tmp_path, engine, now=datetime(2026, 11, 21, 19, 0, tzinfo=UTC))  # warning valid until 21:00 CET
    _, sent = deliver(tmp_path, engine, now=datetime(2026, 11, 21, 22, 30, tzinfo=UTC))  # 23:30 CET: quiet
    assert sent == []
    assert EngineStore(tmp_path / "data").load_channel_state("tg_alerts")["held"] == 1
    _, later = deliver(tmp_path, engine, now=datetime(2026, 11, 22, 5, 30, tzinfo=UTC))  # 06:30 CET
    assert len(later) == 1


def test_bad_chat_marks_only_that_channel_failed(tmp_path):
    engine = engine_for(tmp_path, topics=TOPICS, channels=CHANNELS)
    collect(tmp_path, engine)
    statuses, _ = deliver(tmp_path, engine, response=httpx.Response(
        403, json={"ok": False, "description": "Forbidden: bot is not a member of the channel chat"}))
    assert set(statuses.values()) == {"failed"}
    state = EngineStore(tmp_path / "data").load_channel_state("tg_tenders")
    assert state["sent"] == {} and "not a member" in state["reason"] and TOKEN not in json.dumps(state)


def test_test_message_per_channel(tmp_path):
    engine = engine_for(tmp_path, topics=TOPICS, channels=CHANNELS)
    _, sent = deliver(tmp_path, engine, test=True)
    tests = [m for m in sent if m["text"].startswith("✅")]
    assert {m["chat_id"] for m in tests} == {"@alerts", "@tenders"}


# --- vulns (v1 Global Critical) through the v2 channel layer -----------------------------------------

def _gc_digest(at):
    item = DigestItem(id="CVE-2026-9999", core_text="f5 nginx", product="f5 nginx", cvss=9.8, severity="critical",
                      title="nginx RCE", links=["https://nvd.nist.gov/vuln/detail/CVE-2026-9999"])
    return Digest(topic="vulns", run_id=at.strftime("%Y%m%dT%H%M%SZ"), created_utc=at, title="t", summarised=False,
                  markdown="", html="", items=[item])


def test_vulns_global_critical_alert_via_v2_and_v1_state_migration(tmp_path):
    channels = {"feed": {"type": "feed"},
                "tg_security": {"type": "telegram", "bot_token_env": "TELEGRAM_BOT_TOKEN", "chat_id_env": "TG_SEC",
                                "url": "https://t.me/vulnerabilitiesbrief"}}
    engine = engine_for(tmp_path, topics={"vulns": {"publish_to": ["feed", "tg_security"]}}, channels=channels)
    store = EngineStore(tmp_path / "data")
    old = _gc_digest(NOW - timedelta(hours=3))
    new = _gc_digest(NOW - timedelta(hours=1))
    new.items[0].id = "CVE-2026-8888"
    store.save_digest(old)
    store.save_digest(new)
    # v1 already posted the older alert: the upgrade must not post it again
    store.save_delivery("telegram", {"alerts": {f"{old.run_id}:CVE-2026-9999": {"message_id": 1}}, "briefs": {}})
    _, sent = deliver(tmp_path, engine)
    texts = [m["text"] for m in sent]
    assert len(texts) == 1 and "CVE-2026-8888" in texts[0] and texts[0].startswith("🔴 <b>Critical</b>")
    assert sent[0]["chat_id"] == "@security"
    state = store.load_channel_state("tg_security")
    assert state["migrated_from"] == "telegram.json" and f"vulns:alert:{new.run_id}:CVE-2026-8888" in state["sent"]
