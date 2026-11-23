"""Webhooks, pipeline, nightly and scheduler. Temporary DB, fake agent, no real sending."""
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import agent  # noqa: E402
import drafts  # noqa: E402
import nightly  # noqa: E402
import sender  # noqa: E402
import server  # noqa: E402

SECRET = "test-secret"
AUTH = {"X-Webhook-Secret": SECRET}


@pytest.fixture
def env(tmp_db, monkeypatch):
    """Temporary DB, fake agent, and a sender that records (it must never be called)."""
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("RISK_THRESHOLD", "50")
    monkeypatch.delenv("DEMO_DRAFTS", raising=False)
    agent_calls, sends = [], []
    from db import connect
    connect().close()  # run the migration so the events table exists

    def fake_draft(member_id, reasons, client=None):
        agent_calls.append(member_id)
        return SimpleNamespace(message=f"Hi member {member_id}", channel="whatsapp", offer_id=None,
                               angle="check_in", reasoning_for_staff="test")

    monkeypatch.setattr(agent, "draft_winback", fake_draft)
    monkeypatch.setattr(sender.MockSender, "send", lambda self, *a: sends.append(a) or "x")
    return SimpleNamespace(db=tmp_db, agent_calls=agent_calls, sends=sends)


@pytest.fixture
def client(env):
    return TestClient(server.create_app())


def sql(path, query, params=()):
    db = sqlite3.connect(path)
    rows = db.execute(query, params).fetchall()
    db.commit()
    db.close()
    return rows


def healthy(path, member_id):
    """Give a member a visit every other day for 10 weeks, so they score 0."""
    now = datetime.now()
    for d in range(1, 71, 2):
        sql(path, "INSERT INTO checkins (member_id, ts) VALUES (?, ?)",
            (member_id, (now - timedelta(days=d)).strftime("%Y-%m-%d %H:%M")))


def event(type_, member_id, **extra):
    return {"type": type_, "member_id": member_id, **extra}


def sent_draft(path, member_id, days_ago=2):
    d = SimpleNamespace(message="Hi", channel="whatsapp", offer_id=None, angle="check_in", reasoning_for_staff="r")
    did = drafts.create_draft(member_id, 60, ["flag"], d)
    drafts.approve_and_send(did, "staff", sender=sender.MockSender())
    sql(path, "UPDATE drafts SET sent_at = ? WHERE id = ?",
        ((datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds"), did))
    return did


# ---------- security ----------

@pytest.mark.parametrize("headers", [{}, {"X-Webhook-Secret": "wrong"}])
def test_bad_secret_rejected_on_both_webhooks(client, headers):
    assert client.post("/webhooks/gym-events", json=event("check_in", 1), headers=headers).status_code == 401
    assert client.post("/webhooks/message-status", json={"draft_id": 1, "status": "read"},
                       headers=headers).status_code == 401


def test_correct_secret_passes(client):
    assert client.post("/webhooks/gym-events", json=event("check_in", 1), headers=AUTH).status_code == 202
    # authenticated, but no such sent draft
    assert client.post("/webhooks/message-status", json={"draft_id": 99, "status": "read"},
                       headers=AUTH).status_code == 404


def test_health_needs_no_secret(client):
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("value", ["", "   "])
def test_server_refuses_to_start_without_secret(env, monkeypatch, value):
    monkeypatch.setenv("WEBHOOK_SECRET", value)
    with pytest.raises(RuntimeError, match="WEBHOOK_SECRET"):
        server.create_app()
    monkeypatch.delenv("WEBHOOK_SECRET")
    with pytest.raises(RuntimeError):
        server.create_app()


# ---------- validation ----------

def test_unknown_type_and_unknown_member(client):
    assert client.post("/webhooks/gym-events", json=event("party", 1), headers=AUTH).status_code == 422
    assert client.post("/webhooks/gym-events", json=event("check_in", 999), headers=AUTH).status_code == 404


def test_duplicate_event_id_processed_once(client, env):
    body = event("cancellation_request", 1, event_id="evt-1")
    assert client.post("/webhooks/gym-events", json=body, headers=AUTH).status_code == 202
    r = client.post("/webhooks/gym-events", json=body, headers=AUTH)
    assert r.status_code == 200 and r.json() == {"duplicate": True}
    assert sql(env.db, "SELECT COUNT(*) FROM events")[0][0] == 1
    assert sql(env.db, "SELECT COUNT(*) FROM drafts")[0][0] == 1


# ---------- event handling ----------

def test_check_in_adds_checkin_and_marks_won_back(client, env):
    did = sent_draft(env.db, 1)
    before = sql(env.db, "SELECT COUNT(*) FROM checkins WHERE member_id = 1")[0][0]
    assert client.post("/webhooks/gym-events", json=event("check_in", 1), headers=AUTH).status_code == 202
    assert sql(env.db, "SELECT COUNT(*) FROM checkins WHERE member_id = 1")[0][0] == before + 1
    assert drafts.get_draft(did)["won_back"] == 1


def test_payment_failed_sets_date_and_drafts(client, env):
    # member 1 has no check-ins (35) + failed payment (20) = 55, above 50
    assert client.post("/webhooks/gym-events", json=event("payment_failed", 1), headers=AUTH).status_code == 202
    assert sql(env.db, "SELECT payment_failed_date FROM members WHERE id = 1")[0][0] == date.today().isoformat()
    [d] = drafts.list_pending()
    assert d["member_id"] == 1 and d["trigger"] == "payment_failed" and d["status"] == "pending"
    assert "A payment failed in the last 30 days" in d["reasons"]


def test_payment_failed_below_threshold_makes_no_draft(client, env):
    healthy(env.db, 3)  # 0 + 20 = 20, below 50, and payment_failed is not forced
    client.post("/webhooks/gym-events", json=event("payment_failed", 3), headers=AUTH)
    assert drafts.list_pending() == []


def test_cancellation_forces_draft_below_threshold(client, env):
    healthy(env.db, 3)  # 0 + 40 = 40, below 50
    client.post("/webhooks/gym-events", json=event("cancellation_request", 3), headers=AUTH)
    [d] = drafts.list_pending()
    assert d["member_id"] == 3 and d["trigger"] == "cancellation_request" and d["score"] == 40
    assert d["reasons"] == ["Asked to cancel their membership"]


def test_freeze_forces_draft(client, env):
    healthy(env.db, 3)
    client.post("/webhooks/gym-events", json=event("freeze_request", 3), headers=AUTH)
    [d] = drafts.list_pending()
    assert d["trigger"] == "freeze_request" and d["reasons"] == ["Asked to freeze their membership"]


def test_cancellation_respects_14_day_rule_and_warns(client, env, caplog):
    d = SimpleNamespace(message="Hi", channel="whatsapp", offer_id=None, angle="check_in", reasoning_for_staff="r")
    drafts.create_draft(2, 60, ["flag"], d)
    with caplog.at_level(logging.WARNING, logger="pipeline"):
        client.post("/webhooks/gym-events", json=event("cancellation_request", 2), headers=AUTH)
    assert sql(env.db, "SELECT COUNT(*) FROM drafts WHERE member_id = 2")[0][0] == 1
    assert env.agent_calls == []
    assert any("needs human attention" in r.message and "Member 2" in r.message for r in caplog.records)


def test_membership_renewed(client, env):
    sql(env.db, "UPDATE members SET payment_failed_date = ? WHERE id = 1", (date.today().isoformat(),))
    new_end = (date.today() + timedelta(days=90)).isoformat()
    r = client.post("/webhooks/gym-events", headers=AUTH,
                    json=event("membership_renewed", 1, payload={"new_contract_end": new_end}))
    assert r.status_code == 202
    assert sql(env.db, "SELECT contract_end, payment_failed_date FROM members WHERE id = 1")[0] == (new_end, None)


@pytest.mark.parametrize("value", ["not-a-date", None, (date.today() - timedelta(days=1)).isoformat(),
                                   date.today().isoformat()])
def test_membership_renewed_rejects_bad_dates(client, env, value):
    r = client.post("/webhooks/gym-events", headers=AUTH,
                    json=event("membership_renewed", 1, payload={"new_contract_end": value}))
    assert r.status_code == 422
    assert sql(env.db, "SELECT contract_end FROM members WHERE id = 1")[0][0] == "2027-01-01"
    assert sql(env.db, "SELECT COUNT(*) FROM events")[0][0] == 0


def test_message_status_only_for_sent_drafts(client, env):
    sent = sent_draft(env.db, 1)
    d = SimpleNamespace(message="Hi", channel="whatsapp", offer_id=None, angle="check_in", reasoning_for_staff="r")
    pending = drafts.create_draft(2, 60, ["flag"], d)

    r = client.post("/webhooks/message-status", json={"draft_id": sent, "status": "read", "replied": True},
                    headers=AUTH)
    assert r.status_code == 200 and r.json() == {"updated": True}
    row = drafts.get_draft(sent)
    assert row["delivery_status"] == "read" and row["replied"] == 1 and row["won_back"] == 0
    # a later status without a reply does not erase the reply
    client.post("/webhooks/message-status", json={"draft_id": sent, "status": "delivered"}, headers=AUTH)
    assert drafts.get_draft(sent)["replied"] == 1

    r = client.post("/webhooks/message-status", json={"draft_id": pending, "status": "delivered"}, headers=AUTH)
    assert r.status_code == 404
    assert drafts.get_draft(pending)["delivery_status"] is None


def test_responses_never_include_personal_details(client, env):
    for t in ("payment_failed", "cancellation_request", "check_in"):
        text = client.post("/webhooks/gym-events", json=event(t, 1), headers=AUTH).text
        assert "+91" not in text and "Priya" not in text and "Hi member" not in text


def test_no_webhook_ever_sends(client, env):
    healthy(env.db, 3)
    renew = {"new_contract_end": (date.today() + timedelta(days=30)).isoformat()}
    for t, m in [("payment_failed", 1), ("cancellation_request", 2), ("freeze_request", 3),
                 ("check_in", 1), ("membership_renewed", 2)]:
        client.post("/webhooks/gym-events", json=event(t, m, payload=renew), headers=AUTH)
    statuses = [r[0] for r in sql(env.db, "SELECT status FROM drafts")]
    assert statuses and set(statuses) == {"pending"}
    assert env.sends == []


# ---------- pipeline / nightly / scheduler ----------

def test_pipeline_failure_for_one_member_does_not_stop_others(env, monkeypatch, caplog):
    from pipeline import run_pipeline

    def flaky(member_id, reasons, client=None):
        if member_id == 2:
            raise agent.AgentError("boom")
        return SimpleNamespace(message="Hi", channel="whatsapp", offer_id=None, angle="check_in",
                               reasoning_for_staff="r")

    monkeypatch.setattr(agent, "draft_winback", flaky)
    r = run_pipeline(threshold=0, trigger="nightly")
    assert (r.scored, r.drafted, r.failed) == (3, 2, 1)
    assert "+91" not in caplog.text


def test_nightly_respects_limit_and_skips_recent(env, monkeypatch):
    monkeypatch.setenv("NIGHTLY_LIMIT", "1")
    sql(env.db, "UPDATE members SET payment_failed_date = ?", (date.today().isoformat(),))  # all score 55
    d = SimpleNamespace(message="Hi", channel="whatsapp", offer_id=None, angle="check_in", reasoning_for_staff="r")
    drafts.create_draft(1, 55, ["flag"], d)  # member 1 was contacted recently

    summary = nightly.run_nightly()
    assert summary["drafted"] == 1 and summary["flagged"] == 3
    assert 1 not in env.agent_calls and len(env.agent_calls) == 1
    assert [r[0] for r in sql(env.db, 'SELECT "trigger" FROM drafts ORDER BY id')] == ["manual", "nightly"]


def test_scheduler_registers_one_nightly_job(env, monkeypatch):
    monkeypatch.delenv("DISABLE_SCHEDULER")
    app = server.create_app()
    jobs = app.state.scheduler.get_jobs()
    assert len(jobs) == 1 and jobs[0].id == "nightly"
    assert jobs[0].max_instances == 1 and jobs[0].coalesce is True
    with TestClient(app):
        assert app.state.scheduler.running
    assert not app.state.scheduler.running  # shut down cleanly


def test_scheduler_disabled(env):
    assert server.create_app().state.scheduler is None


def test_placeholder_for_cancellation_offers_pause():
    from pipeline import demo_draft
    m = {"name": "Rohan Nair", "fav_class": "Yoga", "usual_time": "morning"}
    d = demo_draft(m, ["Asked to cancel their membership"])
    assert d.angle == "pause_plan" and d.offer_id == "offer_pause_30" and "missed you" not in d.message
