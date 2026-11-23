import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import drafts  # noqa: E402
import run_flow  # noqa: E402
from sender import MockSender  # noqa: E402


class FakeSender:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send(self, phone, message, channel):
        if self.fail:
            raise RuntimeError("provider down")
        self.sent.append((phone, message, channel))
        return "id-1"


def make(member_id=1, score=70, message="Hi Priya, see you at Yoga?"):
    d = SimpleNamespace(message=message, channel="whatsapp", offer_id=None, angle="check_in",
                        reasoning_for_staff="Test reason")
    return drafts.create_draft(member_id, score, ["No check-in for 20 days"], d)


def test_create_and_list_sorted_with_branch_filter(tmp_db):
    a, b, c = make(1, 60), make(2, 90), make(3, 75)
    assert [d["id"] for d in drafts.list_pending()] == [b, c, a]
    assert drafts.list_pending()[0]["member_name"] == "Rahul Nair"
    assert [d["id"] for d in drafts.list_pending("hsr")] == [a]
    assert [d["id"] for d in drafts.list_pending("koramangala")] == [b]
    assert drafts.get_draft(a)["reasons"] == ["No check-in for 20 days"]


def test_approve_and_send(tmp_db):
    did, s = make(), FakeSender()
    drafts.approve_and_send(did, "asha", sender=s)
    d = drafts.get_draft(did)
    assert d["status"] == "sent" and d["reviewer"] == "asha"
    assert d["decided_at"] and d["sent_at"] and d["edited_message"] is None
    assert s.sent == [("+91 9811111111", "Hi Priya, see you at Yoga?", "whatsapp")]


def test_edit_keeps_original_and_sends_edited(tmp_db):
    did, s = make(), FakeSender()
    drafts.approve_and_send(did, "asha", final_message="Hi Priya, new text", sender=s)
    d = drafts.get_draft(did)
    assert d["message"] == "Hi Priya, see you at Yoga?" and d["edited_message"] == "Hi Priya, new text"
    assert s.sent[0][1] == "Hi Priya, new text"


def test_same_text_is_not_an_edit(tmp_db):
    did = make()
    drafts.approve_and_send(did, "asha", final_message="Hi Priya, see you at Yoga?", sender=FakeSender())
    assert drafts.get_draft(did)["edited_message"] is None


def test_reject_sends_nothing(tmp_db):
    did = make()
    drafts.reject(did, "asha", "too pushy")
    d = drafts.get_draft(did)
    assert d["status"] == "rejected" and d["reject_reason"] == "too pushy"
    assert d["reviewer"] == "asha" and d["decided_at"] and d["sent_at"] is None


def test_double_approve_sends_once(tmp_db):
    did, s = make(), FakeSender()
    drafts.approve_and_send(did, "asha", sender=s)
    with pytest.raises(drafts.DraftError):
        drafts.approve_and_send(did, "asha", sender=s)
    assert len(s.sent) == 1


def test_closed_drafts_cannot_be_approved_or_rejected(tmp_db):
    sent, rejected = make(1), make(2)
    drafts.approve_and_send(sent, "a", sender=FakeSender())
    drafts.reject(rejected, "a", "no")
    for did in (sent, rejected):
        with pytest.raises(drafts.DraftError):
            drafts.approve_and_send(did, "a", sender=FakeSender())
        with pytest.raises(drafts.DraftError):
            drafts.reject(did, "a", "again")


def test_sender_failure_leaves_draft_pending(tmp_db):
    did = make()
    with pytest.raises(RuntimeError):
        drafts.approve_and_send(did, "asha", sender=FakeSender(fail=True))
    d = drafts.get_draft(did)
    assert d["status"] == "pending" and d["sent_at"] is None
    drafts.approve_and_send(did, "asha", sender=FakeSender())  # can retry
    assert drafts.get_draft(did)["status"] == "sent"


def test_recently_drafted(tmp_db):
    pending, rejected, old = make(1), make(2), make(3)
    drafts.reject(rejected, "a", "no")
    db = sqlite3.connect(tmp_db)
    db.execute("UPDATE drafts SET created_at = ? WHERE id = ?",
               ((datetime.now() - timedelta(days=20)).isoformat(timespec="seconds"), old))
    db.commit()
    db.close()
    assert drafts.recently_drafted(1)          # pending
    assert not drafts.recently_drafted(2)      # rejected
    assert not drafts.recently_drafted(3)      # older than 14 days
    drafts.approve_and_send(pending, "a", sender=FakeSender())
    assert drafts.recently_drafted(1)          # sent


def test_demo_draft_refused_by_real_sender(tmp_db):
    did = make(message="[DEMO] placeholder")
    with pytest.raises(drafts.DraftError):
        drafts.approve_and_send(did, "a", sender=FakeSender())
    assert drafts.get_draft(did)["status"] == "pending"


def test_mock_sender_format(capsys):
    MockSender().send("+91 9811111111", "Hello there", "whatsapp")
    assert capsys.readouterr().out == "SENT via whatsapp to +91 9811111111: Hello there\n"


def test_migration_adds_columns_without_losing_data(tmp_db):
    db = sqlite3.connect(tmp_db)
    db.execute("INSERT INTO drafts (member_id, score, message, status) VALUES (1, 55, 'old draft', 'pending')")
    db.commit()
    db.close()
    d = drafts.list_pending()[0]  # connect() runs the migration
    assert d["message"] == "old draft" and d["channel"] is None and "decided_at" in d


def test_review_loop_end_to_end(tmp_db, monkeypatch):
    ids = [make(1, 90), make(2, 80), make(3, 70), make(1, 60, message="skip me")]
    answers = iter(["x",                      # invalid input, asked again
                    "a",                      # approve #1
                    "e", "My own words", "y", # edit #2
                    "r", "", "too generic",   # reject #3 (blank reason re-asked)
                    "s"])                     # skip #4
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    sender = FakeSender()
    counts = run_flow.review(None, "asha", sender=sender)
    assert counts == {"sent": 1, "edited": 1, "rejected": 1, "skipped": 1}
    status = {i: drafts.get_draft(i) for i in ids}
    assert status[ids[0]]["status"] == "sent"
    assert status[ids[1]]["status"] == "sent" and status[ids[1]]["edited_message"] == "My own words"
    assert status[ids[2]]["status"] == "rejected" and status[ids[2]]["reject_reason"] == "too generic"
    assert status[ids[3]]["status"] == "pending"
    assert [s[1] for s in sender.sent] == ["Hi Priya, see you at Yoga?", "My own words"]


def test_ctrl_c_leaves_drafts_pending(tmp_db, monkeypatch):
    did = make()

    def interrupt(prompt=""):
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", interrupt)
    run_flow.review(None, "asha", sender=FakeSender())
    assert drafts.get_draft(did)["status"] == "pending"
