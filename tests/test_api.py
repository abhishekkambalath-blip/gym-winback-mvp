"""Dashboard API. Temporary DB, fake agent, fake sender: no real API calls, nothing real sent."""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))
import agent  # noqa: E402
import drafts  # noqa: E402
import sender  # noqa: E402
import server  # noqa: E402

AUTH = {"X-Webhook-Secret": "ui-secret"}


@pytest.fixture
def ui(tmp_db, monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "ui-secret")
    monkeypatch.setenv("DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("DEMO_DRAFTS", "0")
    sends = []
    monkeypatch.setattr(sender.MockSender, "send", lambda self, *a: sends.append(a) or "x")
    monkeypatch.setattr(agent, "draft_winback", lambda mid, reasons, client=None: SimpleNamespace(
        message=f"Hi member {mid}", channel="whatsapp", offer_id=None, angle="check_in", reasoning_for_staff="why"))
    return SimpleNamespace(client=TestClient(server.create_app()), sends=sends)


def make_draft(member_id=1, score=60):
    d = SimpleNamespace(message="Hi Priya, see you at Yoga?", channel="whatsapp", offer_id=None,
                        angle="check_in", reasoning_for_staff="why")
    return drafts.create_draft(member_id, score, ["No check-in for 20 days"], d)


def test_api_needs_the_secret(ui):
    for method, path in [("get", "/api/status"), ("get", "/api/drafts"), ("get", "/api/stats"),
                         ("get", "/api/at-risk"), ("post", "/api/drafts/1/approve"), ("post", "/api/nightly")]:
        assert getattr(ui.client, method)(path).status_code == 401, path


def test_page_and_assets_are_served(ui):
    page = ui.client.get("/")
    assert page.status_code == 200 and "Win-Back Desk" in page.text
    assert ui.client.get("/static/style.css").status_code == 200
    assert ui.client.get("/static/app.js").status_code == 200


def test_status_modes(ui, monkeypatch):
    s = ui.client.get("/api/status", headers=AUTH).json()
    assert s["drafting"] == "claude" and s["sender"] == "mock" and s["pending"] == 0
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("DEMO_DRAFTS", "0")
    placeholder = TestClient(server.create_app())  # no key: switches itself to placeholders
    assert placeholder.get("/api/status", headers=AUTH).json()["drafting"] == "placeholder"


def test_drafts_listing_has_no_phone_numbers(ui):
    make_draft()
    text = ui.client.get("/api/drafts?status=pending", headers=AUTH).text
    assert "+91" not in text and "9811111111" not in text and "phone" not in text
    [d] = ui.client.get("/api/drafts?status=pending", headers=AUTH).json()
    assert d["member_name"] == "Priya Sharma" and d["reasons"] == ["No check-in for 20 days"]


def test_approve_with_edit_sends_edited_text(ui):
    did = make_draft()
    r = ui.client.post(f"/api/drafts/{did}/approve", headers=AUTH, json={"message": "My version", "reviewer": "Asha"})
    assert r.status_code == 200
    d = drafts.get_draft(did)
    assert d["status"] == "sent" and d["reviewer"] == "Asha" and d["edited_message"] == "My version"
    assert d["message"] == "Hi Priya, see you at Yoga?"
    assert ui.sends == [("+91 9811111111", "My version", "whatsapp")]


def test_approve_twice_is_a_conflict_and_sends_once(ui):
    did = make_draft()
    assert ui.client.post(f"/api/drafts/{did}/approve", headers=AUTH, json={}).status_code == 200
    assert ui.client.post(f"/api/drafts/{did}/approve", headers=AUTH, json={}).status_code == 409
    assert len(ui.sends) == 1


def test_reject_needs_reason_and_sends_nothing(ui):
    did = make_draft()
    assert ui.client.post(f"/api/drafts/{did}/reject", headers=AUTH, json={"reason": ""}).status_code == 422
    r = ui.client.post(f"/api/drafts/{did}/reject", headers=AUTH, json={"reason": "too pushy", "reviewer": "Asha"})
    assert r.status_code == 200
    d = drafts.get_draft(did)
    assert d["status"] == "rejected" and d["reject_reason"] == "too pushy" and ui.sends == []


def test_at_risk_and_manual_draft(ui):
    rows = ui.client.get("/api/at-risk?threshold=30", headers=AUTH).json()
    assert {r["member_id"] for r in rows} == {1, 2, 3} and all(r["score"] == 35 and not r["drafted"] for r in rows)
    assert "phone" not in str(rows)
    assert ui.client.get("/api/at-risk", headers=AUTH).json() == []  # default threshold 50

    r = ui.client.post("/api/members/1/draft", headers=AUTH)
    assert r.status_code == 200 and r.json()["drafted"] == 1
    [d] = drafts.list_pending()
    assert d["member_id"] == 1 and d["trigger"] == "manual"
    assert ui.client.post("/api/members/1/draft", headers=AUTH).status_code == 409  # 14-day rule
    assert ui.client.post("/api/members/999/draft", headers=AUTH).status_code == 404
    assert ui.sends == []  # drafting never sends


def test_stats_with_no_data_and_with_data(ui):
    s = ui.client.get("/api/stats", headers=AUTH).json()
    assert s["total"]["win_rate"] == "n/a" and len(s["branches"]) == 3 and len(s["angles"]) == 5
    did = make_draft()
    ui.client.post(f"/api/drafts/{did}/approve", headers=AUTH, json={})
    s = ui.client.get("/api/stats", headers=AUTH).json()
    assert s["total"]["sent"] == 1 and s["total"]["win_rate"] == "0%"
