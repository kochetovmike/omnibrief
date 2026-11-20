"""Telegram publisher: Global Critical alerts + the twice-daily Global brief. Fake Bot API (respx);
no real token ever leaves the tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from omnibrief.models import Digest, DigestItem
from omnibrief.pipeline import deliver
from omnibrief.publishers.base import PublisherContext
from omnibrief.publishers.telegram import TelegramPublisher
from omnibrief.state import Store

TOKEN = "123456:SECRET-test-token"
SEND = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
ENV = {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_CHAT_ID": "@vulnerabilitiesbrief"}
# 20 Nov 2026 is CET (UTC+1): the 08:00–20:00 window is 07:00–19:00 UTC.
RUN_AT = datetime(2026, 11, 20, 9, 17, tzinfo=UTC)


def item(id_, *, core, cvss=None, severity=None, kev=False, **kw):
    return DigestItem(id=id_, core_text=core, product=core, cvss=cvss, severity=severity, kev=kev,
                      title=kw.pop("title", f"title of {id_}"),
                      links=kw.pop("links", [f"https://nvd.nist.gov/vuln/detail/{id_}"]), **kw)


def digest(at, *items, html="", summarised=False):
    return Digest(topic="vulns", run_id=at.strftime("%Y%m%dT%H%M%SZ"), created_utc=at, title="t",
                  summarised=summarised, markdown="", html=html, items=list(items))


CRIT = item("CVE-2026-1000", core="f5 nginx", cvss=9.8, severity="critical")
KEV = item("CVE-2026-1001", core="openbsd openssh", cvss=8.1, severity="high", kev=True,
           kev_date_added="2026-11-19")
NOTABLE = item("CVE-2026-1002", core="openssl openssl", cvss=7.5, severity="high")
OTHER = item("CVE-2026-1003", core="acme widget", cvss=9.8, severity="critical")  # not fundamental


def pub(cfg, env=ENV, now=RUN_AT) -> TelegramPublisher:
    ctx = PublisherContext(cfg=cfg, topic=cfg.topic("vulns"), site_dir=None, env=env, now=now)
    return TelegramPublisher("telegram", cfg.publishers["telegram"], ctx)


def ok(message_id=1):
    return httpx.Response(200, json={"ok": True, "result": {"message_id": message_id}})


def sent_texts(route):
    return [json.loads(c.request.content)["text"] for c in route.calls]


# --- enablement -----------------------------------------------------------------------------------

def test_disabled_without_token_or_chat(cfg):
    assert pub(cfg, env={}).is_enabled() == (False, "TELEGRAM_BOT_TOKEN not set")
    assert pub(cfg, env={"TELEGRAM_BOT_TOKEN": TOKEN}).is_enabled() == (False, "TELEGRAM_CHAT_ID not set")
    assert pub(cfg).is_enabled() == (True, "configured")


def test_deliver_without_config_records_disabled_and_sends_nothing(cfg, tmp_path):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(url__startswith="https://api.telegram.org").mock(return_value=ok())
        assert deliver(cfg, tmp_path, env={}, now=RUN_AT, client=httpx.Client()) == {"telegram": "disabled"}
    assert route.call_count == 0
    assert Store(tmp_path).load_delivery("telegram")["status"] == "disabled"


# --- alerts ----------------------------------------------------------------------------------------

@respx.mock
def test_alerts_only_for_global_critical_once_each(cfg):
    route = respx.post(SEND).mock(side_effect=[ok(11), ok(12)])
    d = digest(RUN_AT - timedelta(hours=1), CRIT, KEV, NOTABLE, OTHER)
    p = pub(cfg)
    result, state = p.deliver([d], {}, httpx.Client(), RUN_AT, sleep=lambda s: None)
    texts = sent_texts(route)
    assert result.status == "ok" and len(texts) == 2  # NOTABLE waits for the brief; OTHER isn't global
    assert texts[0].startswith("🔴 <b>Critical</b> · <code>CVE-2026-1000</code>")
    assert texts[1].startswith("🚨 <b>Actively exploited</b> · <code>CVE-2026-1001</code>")
    assert "in CISA KEV since 2026-11-19" in texts[1]
    body = json.loads(route.calls[0].request.content)
    assert body["chat_id"] == "@vulnerabilitiesbrief" and body["parse_mode"] == "HTML"
    assert body["link_preview_options"] == {"is_disabled": True}
    # second deliver: nothing new
    result2, state2 = p.deliver([d], state, httpx.Client(), RUN_AT + timedelta(hours=1), sleep=lambda s: None)
    assert route.call_count == 2 and result2.delivered == []
    assert state2["alerts"][f"{d.run_id}:CVE-2026-1000"]["message_id"] == 11


@respx.mock
def test_old_alerts_are_not_sent_after_an_outage_or_on_first_enable(cfg):
    route = respx.post(SEND).mock(return_value=ok())
    old = digest(RUN_AT - timedelta(hours=30), CRIT)
    pub(cfg).deliver([old], {}, httpx.Client(), RUN_AT, sleep=lambda s: None)
    assert route.call_count == 0


def test_alert_escapes_text_and_drops_non_https_links(cfg):
    evil = item("CVE-2026-2000", core="nginx <b>x</b>", cvss=9.9, severity="critical",
                title='<script>alert(1)</script> & "quotes"',
                links=["javascript:alert(1)", "https://nginx.org/en/security_advisories.html?a=1&b=2"])
    text = pub(cfg).render_alert(digest(RUN_AT, evil), evil)
    assert "<script>" not in text and "&lt;script&gt;" in text and "&amp;" in text
    assert "javascript:" not in text
    assert '<a href="https://nginx.org/en/security_advisories.html?a=1&amp;b=2">nginx.org</a>' in text
    assert "about.html" in text and "global-critical" in text  # attribution + dashboard link


def test_alert_uses_llm_sentence_when_available(cfg):
    html = "<ul><li><strong>CVE-2026-1000</strong> lets attackers run code via HTTP/3; update to 1.27.3.</li></ul>"
    text = pub(cfg).render_alert(digest(RUN_AT, CRIT, html=html, summarised=True), CRIT)
    assert "lets attackers run code via HTTP/3; update to 1.27.3." in text


# --- Global brief -------------------------------------------------------------------------------------

@respx.mock
def test_brief_after_window_closes_with_notable_items_only(cfg):
    route = respx.post(SEND).mock(return_value=ok(50))
    p = pub(cfg)
    in_window = digest(datetime(2026, 11, 20, 10, 17, tzinfo=UTC), NOTABLE, CRIT, OTHER)
    # 18:17 UTC = 19:17 CET: window still open -> no brief (the alert goes out)
    _, state = p.deliver([in_window], {}, httpx.Client(), datetime(2026, 11, 20, 18, 17, tzinfo=UTC),
                         sleep=lambda s: None)
    assert [t[:12] for t in sent_texts(route)] == ["🔴 <b>Critica"]
    # 19:17 UTC = 20:17 CET: the 08:00–20:00 window closed -> one brief
    _, state = p.deliver([in_window], state, httpx.Client(), datetime(2026, 11, 20, 19, 17, tzinfo=UTC),
                         sleep=lambda s: None)
    brief = sent_texts(route)[-1]
    assert brief.startswith("🟣 <b>Global brief</b> · 20 Nov, 08:00–20:00 CET")
    assert "1 critical alert already sent" in brief and "CVE-2026-1002" in brief
    assert "CVE-2026-1003" not in brief and "CVE-2026-1000" not in brief.split("worth knowing")[1]
    # not sent twice
    p.deliver([in_window], state, httpx.Client(), datetime(2026, 11, 20, 20, 17, tzinfo=UTC), sleep=lambda s: None)
    assert route.call_count == 2


@respx.mock
def test_empty_window_is_recorded_but_nothing_posted(cfg):
    route = respx.post(SEND).mock(return_value=ok())
    _, state = pub(cfg).deliver([], {}, httpx.Client(), datetime(2026, 11, 20, 19, 17, tzinfo=UTC),
                                sleep=lambda s: None)
    assert route.call_count == 0 and list(state["briefs"].values()) == [{"skipped": "empty",
                                                                         "at_utc": "2026-11-20T19:17:00+00:00"}]


def test_brief_skipped_if_window_closed_long_ago(cfg):
    late = datetime(2026, 11, 21, 2, 17, tzinfo=UTC)  # 03:17 CET: 20:00 window still open, 08:00 one 7 h old
    d = digest(datetime(2026, 11, 20, 10, 17, tzinfo=UTC), NOTABLE)
    assert [k for k, _, _ in pub(cfg).plan([d], {}, late)] == []


def test_brief_stays_under_telegram_limit(cfg):
    many = [item(f"CVE-2026-{3000 + n}", core="linux kernel", cvss=7.8, severity="high", title="x" * 300)
            for n in range(40)]
    p = pub(cfg)
    text = p.render_brief(datetime(2026, 11, 20, 7, tzinfo=UTC), datetime(2026, 11, 20, 19, tzinfo=UTC), many, 0)
    assert len(text) <= 4000 and "+30 more" in text and text.count("• ") == 10


# --- errors -----------------------------------------------------------------------------------------

@respx.mock
def test_rate_limit_honoured_once_then_sent(cfg):
    route = respx.post(SEND).mock(side_effect=[
        httpx.Response(429, json={"ok": False, "description": "Too Many Requests",
                                  "parameters": {"retry_after": 3}}), ok(7)])
    slept = []
    result, _ = pub(cfg).deliver([digest(RUN_AT, CRIT)], {}, httpx.Client(), RUN_AT, sleep=slept.append)
    assert result.status == "ok" and route.call_count == 2 and slept == [3.5]


@respx.mock
def test_bad_chat_or_not_admin_marks_failed_and_keeps_pending(cfg, caplog):
    respx.post(SEND).mock(return_value=httpx.Response(
        403, json={"ok": False, "description": "Forbidden: bot is not a member of the channel chat"}))
    d = digest(RUN_AT, CRIT, KEV)
    result, state = pub(cfg).deliver([d], {}, httpx.Client(), RUN_AT, sleep=lambda s: None)
    assert result.status == "failed" and "not a member" in result.reason
    assert state["alerts"] == {}  # nothing marked sent -> retried next run
    assert TOKEN not in caplog.text and TOKEN not in json.dumps(state)


@respx.mock
def test_deliver_never_raises_and_persists_status(cfg, tmp_path):
    respx.post(SEND).mock(side_effect=httpx.ConnectError("boom"))
    store = Store(tmp_path)
    store.save_digest(digest(RUN_AT - timedelta(hours=1), CRIT))
    statuses = deliver(cfg, tmp_path, env=ENV, now=RUN_AT, client=httpx.Client())
    assert statuses == {"telegram": "pending"}
    assert store.load_delivery("telegram")["reason"].startswith("transient")


@respx.mock
def test_test_message(cfg, tmp_path):
    route = respx.post(SEND).mock(return_value=ok(99))
    deliver(cfg, tmp_path, env=ENV, now=RUN_AT, client=httpx.Client(), test=True)
    assert sent_texts(route)[0].startswith("✅ <b>Omnibrief is connected.</b>")


def test_hub_links_the_channel(cfg):
    from omnibrief.site import telegram_url

    assert telegram_url(cfg, ENV) == "https://t.me/vulnerabilitiesbrief"
    assert telegram_url(cfg, {"TELEGRAM_CHAT_ID": "-1001234"}) is None
    assert telegram_url(cfg, {"TELEGRAM_CHAT_ID": '@x"><script>'}) is None


@pytest.mark.parametrize("core,cvss,kev,tier", [
    ("f5 nginx", 9.8, False, "critical"), ("openbsd openssh", 7.0, True, "critical"),
    ("openssl openssl", 7.5, False, "notable"), ("linux kernel", 6.5, False, None), ("acme", 9.8, True, None),
])
def test_tiers(core, cvss, kev, tier):
    from omnibrief.global_critical import item_tier

    assert item_tier(DigestItem(id="X", core_text=core, cvss=cvss, kev=kev)) == tier
