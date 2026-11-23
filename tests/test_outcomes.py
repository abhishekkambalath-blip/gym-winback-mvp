import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import drafts  # noqa: E402
import feedback  # noqa: E402
import stats  # noqa: E402
import track  # noqa: E402
from sender import MockSender  # noqa: E402
from simulate_checkin import simulate_checkin  # noqa: E402

SENT = datetime(2026, 11, 1, 10, 0)
NOW = datetime(2026, 11, 30, 12, 0)


class FakeSender(MockSender):  # a silent mock, so [DEMO] drafts are allowed
    def send(self, phone, message, channel):
        return "id"


def sent_draft(db_path, member_id, message="Hi there", angle="check_in", sent_at=SENT, edit=None):
    d = SimpleNamespace(message=message, channel="whatsapp", offer_id=None, angle=angle, reasoning_for_staff="r")
    did = drafts.create_draft(member_id, 60, ["flag"], d)
    drafts.approve_and_send(did, "asha", final_message=edit, sender=FakeSender())
    db = sqlite3.connect(db_path)
    db.execute("UPDATE drafts SET sent_at = ? WHERE id = ?", (sent_at.isoformat(timespec="seconds"), did))
    db.commit()
    db.close()
    return did


def checkin(db_path, member_id, at):
    db = sqlite3.connect(db_path)
    db.execute("INSERT INTO checkins (member_id, ts) VALUES (?, ?)", (member_id, at.strftime("%Y-%m-%d %H:%M")))
    db.commit()
    db.close()


# ---------- track ----------

def test_update_outcomes_window_and_before(tmp_db):
    a, b, c = (sent_draft(tmp_db, m) for m in (1, 2, 3))
    checkin(tmp_db, 1, SENT + timedelta(days=3))      # inside window
    checkin(tmp_db, 2, SENT + timedelta(days=20))     # too late
    checkin(tmp_db, 3, SENT - timedelta(hours=2))     # before sending
    assert track.update_outcomes(now=NOW) == 1
    assert drafts.get_draft(a)["won_back"] == 1
    assert drafts.get_draft(a)["won_back_at"] == (SENT + timedelta(days=3)).isoformat(timespec="seconds")
    assert not drafts.get_draft(b)["won_back"] and not drafts.get_draft(c)["won_back"]


def test_first_checkin_after_sending_is_used(tmp_db):
    a = sent_draft(tmp_db, 1)
    checkin(tmp_db, 1, SENT + timedelta(days=6))
    checkin(tmp_db, 1, SENT + timedelta(days=2))
    track.update_outcomes(now=NOW)
    assert drafts.get_draft(a)["won_back_at"].startswith((SENT + timedelta(days=2)).date().isoformat())


def test_update_outcomes_idempotent(tmp_db):
    sent_draft(tmp_db, 1)
    checkin(tmp_db, 1, SENT + timedelta(days=1))
    assert track.update_outcomes(now=NOW) == 1
    assert track.update_outcomes(now=NOW) == 0


def test_pending_and_rejected_ignored(tmp_db):
    d = SimpleNamespace(message="m", channel="whatsapp", offer_id=None, angle="check_in", reasoning_for_staff="r")
    pending = drafts.create_draft(1, 60, [], d)
    rejected = drafts.create_draft(2, 60, [], d)
    drafts.reject(rejected, "a", "no")
    for m in (1, 2):
        checkin(tmp_db, m, datetime.now() + timedelta(minutes=1))
    assert track.update_outcomes(now=datetime.now() + timedelta(days=1)) == 0
    assert not drafts.get_draft(pending)["won_back"] and not drafts.get_draft(rejected)["won_back"]


# ---------- stats ----------

def row(status="sent", won=0, edited=None, sent_at=SENT, angle="check_in", reason=None):
    return {"status": status, "won_back": won, "edited_message": edited, "angle": angle,
            "sent_at": sent_at.isoformat() if status == "sent" else None, "reject_reason": reason}


def test_stats_rates():
    rows = [row(won=1, edited="x"), row(), row(), row(won=1), row("pending"), row("rejected", reason="Too pushy")]
    s = stats.summarize(rows, NOW)
    assert (s["generated"], s["sent"], s["pending"], s["rejected"]) == (6, 4, 1, 1)
    assert s["edited"] == 1 and s["edited_rate"] == "25%"
    assert s["won_back"] == 2 and s["win_rate"] == "50%"
    assert stats.top_reasons(rows) == [("too pushy", 1)]


def test_stats_no_data_shows_na():
    s = stats.summarize([], NOW)
    assert s["edited_rate"] == "n/a" and s["win_rate"] == "n/a" and s["sent"] == 0
    assert all(rate == "n/a" for _, _, _, rate in stats.by_angle([]))
    assert stats.pct(0, 0) == "n/a"


def test_stats_outcome_pending_vs_not_returned():
    rows = [row(sent_at=NOW - timedelta(days=3)),    # still in window
            row(sent_at=NOW - timedelta(days=20)),   # window closed, no return
            row(sent_at=NOW - timedelta(days=20), won=1)]
    s = stats.summarize(rows, NOW)
    assert s["outcome_pending"] == 1 and s["not_returned"] == 1 and s["won_back"] == 1


def test_stats_report_runs_on_empty_db(tmp_db):
    assert "n/a" in stats.report(now=NOW)


# ---------- feedback ----------

def test_feedback_dry_run_write_and_no_duplicates(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(feedback.rag, "build_index", lambda: None)
    kb = tmp_path / "past.md"
    kb.write_text("# Past\n\n## Message 1 | angle: check_in | returned: yes\nOld example\n")
    won = sent_draft(tmp_db, 1, message="Original text", edit="Edited text", angle="free_pt")
    checkin(tmp_db, 1, SENT + timedelta(days=2))
    track.update_outcomes(now=NOW)
    closed = sent_draft(tmp_db, 2, message="No luck text")                          # window closed, no return
    sent_draft(tmp_db, 3, message="Too recent", sent_at=NOW - timedelta(days=2))   # not finished yet

    before = kb.read_text()
    assert len(feedback.export_learnings(write=False, path=kb, now=NOW)) == 2
    assert kb.read_text() == before

    added = feedback.export_learnings(write=True, path=kb, now=NOW)
    text = kb.read_text()
    assert len(added) == 2
    assert f"## Message draft #{won} | angle: free_pt | returned: yes\nEdited text" in text
    assert "Original text" not in text
    assert f"## Message draft #{closed} | angle: check_in | returned: no\nNo luck text" in text
    assert "Too recent" not in text

    assert feedback.export_learnings(write=True, path=kb, now=NOW) == []
    assert kb.read_text() == text


def test_feedback_never_writes_demo_placeholders(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(feedback.rag, "build_index", lambda: None)
    kb = tmp_path / "past.md"
    kb.write_text("# Past\n")
    sent_draft(tmp_db, 1, message="[DEMO] Hi", sent_at=NOW - timedelta(days=20))
    assert len(feedback.export_learnings(write=False, path=kb, now=NOW)) == 1
    assert feedback.export_learnings(write=True, path=kb, now=NOW) == []
    assert kb.read_text() == "# Past\n"


# ---------- simulate_checkin ----------

def test_simulate_checkin_unknown_member(tmp_db):
    with pytest.raises(ValueError, match="No member with id 999"):
        simulate_checkin(999)


def test_simulate_checkin_inserts(tmp_db):
    ts = simulate_checkin(1, datetime(2026, 11, 20, 7, 30))
    assert ts == "2026-11-20 07:30:00"
    db = sqlite3.connect(tmp_db)
    assert db.execute("SELECT COUNT(*) FROM checkins WHERE member_id = 1").fetchone()[0] == 1
