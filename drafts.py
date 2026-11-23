"""Repository for the drafts table. Plain sqlite3, no ORM."""
import json
from datetime import datetime, timedelta

from datasource import get_datasource
from db import connect
from sender import MockSender, get_sender


class DraftError(Exception):
    """A draft could not be approved or rejected (wrong status, missing member, etc.)."""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_dict(row) -> dict:
    d = dict(row)
    d["reasons"] = json.loads(d["reasons"]) if d.get("reasons") else []
    return d


TRIGGERS = ("nightly", "payment_failed", "cancellation_request", "freeze_request", "manual")


def create_draft(member_id: int, score: int, reasons: list[str], draft, trigger: str = "manual") -> int:
    """Always saved as 'pending'. Creating a draft never sends anything."""
    if trigger not in TRIGGERS:
        raise ValueError(f"trigger must be one of {TRIGGERS}, got '{trigger}'")
    db = connect()
    try:
        cur = db.execute(
            "INSERT INTO drafts (member_id, score, reasons, message, status, channel, offer_id,"
            ' angle, reasoning, created_at, "trigger") VALUES (?,?,?,?,\'pending\',?,?,?,?,?,?)',
            (member_id, score, json.dumps(reasons), draft.message, draft.channel, draft.offer_id,
             draft.angle, draft.reasoning_for_staff, _now(), trigger))
        db.commit()
        return cur.lastrowid
    finally:
        db.close()


_SELECT = ("SELECT d.*, m.name AS member_name, m.branch AS branch "
           "FROM drafts d JOIN members m ON m.id = d.member_id ")


def list_pending(branch: str | None = None) -> list[dict]:
    """Pending drafts, highest score first. branch: koramangala | indiranagar | hsr."""
    sql, params = _SELECT + "WHERE d.status = 'pending'", []
    if branch:
        sql += " AND LOWER(m.branch) LIKE ?"
        params.append(branch.lower() + "%")
    db = connect()
    try:
        return [_to_dict(r) for r in db.execute(sql + " ORDER BY d.score DESC, d.id", params)]
    finally:
        db.close()


def get_draft(draft_id: int) -> dict | None:
    db = connect()
    try:
        row = db.execute(_SELECT + "WHERE d.id = ?", (draft_id,)).fetchone()
        return _to_dict(row) if row else None
    finally:
        db.close()


def list_all(status: str | None = None) -> list[dict]:
    sql, params = _SELECT, []
    if status:
        sql += "WHERE d.status = ? "
        params.append(status)
    db = connect()
    try:
        return [_to_dict(r) for r in db.execute(sql + "ORDER BY d.id", params)]
    finally:
        db.close()


def recently_drafted(member_id: int, days: int = 14) -> bool:
    """True if the member got a pending/approved/sent draft in the last `days` days."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    db = connect()
    try:
        row = db.execute(
            "SELECT 1 FROM drafts WHERE member_id = ? AND created_at >= ?"
            " AND status IN ('pending','approved','sending','sent') LIMIT 1", (member_id, cutoff)).fetchone()
        return row is not None
    finally:
        db.close()


def approve_and_send(draft_id: int, reviewer: str, final_message: str | None = None, sender=None) -> None:
    """The ONLY place a message is sent. Works only on a pending draft."""
    sender = sender or get_sender()
    draft = get_draft(draft_id)
    if draft is None:
        raise DraftError(f"Draft {draft_id} does not exist")
    if draft["status"] != "pending":
        raise DraftError(f"Draft {draft_id} is '{draft['status']}', not pending")

    edited = final_message.strip() if final_message and final_message.strip() != draft["message"].strip() else None
    text = edited or draft["message"]
    if text.startswith("[DEMO]") and not isinstance(sender, MockSender):
        raise DraftError("Refusing to send a [DEMO] placeholder with a real sender")
    member = get_datasource().get_member(draft["member_id"])
    if member is None:
        raise DraftError(f"Member {draft['member_id']} not found")

    db = connect()
    try:
        # Claim the draft first so a second approval cannot also send it.
        claimed = db.execute("UPDATE drafts SET status = 'sending' WHERE id = ? AND status = 'pending'",
                             (draft_id,)).rowcount
        db.commit()
        if claimed != 1:
            raise DraftError(f"Draft {draft_id} is no longer pending")
        try:
            sender.send(member["phone"], text, draft["channel"] or "whatsapp")
        except Exception:
            db.execute("UPDATE drafts SET status = 'pending' WHERE id = ?", (draft_id,))
            db.commit()
            raise
        now = _now()
        db.execute("UPDATE drafts SET status = 'sent', edited_message = ?, reviewer = ?, decided_at = ?,"
                   " sent_at = ? WHERE id = ?", (edited, reviewer, now, now, draft_id))
        db.commit()
    finally:
        db.close()


def reject(draft_id: int, reviewer: str, reason: str) -> None:
    if not reason or not reason.strip():
        raise DraftError("A rejection reason is required")
    db = connect()
    try:
        done = db.execute(
            "UPDATE drafts SET status = 'rejected', reject_reason = ?, reviewer = ?, decided_at = ?"
            " WHERE id = ? AND status = 'pending'", (reason.strip(), reviewer, _now(), draft_id)).rowcount
        db.commit()
        if done != 1:
            raise DraftError(f"Draft {draft_id} does not exist or is not pending")
    finally:
        db.close()
