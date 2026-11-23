"""Track outcomes: did a member check in within 14 days of their message being sent?
Usage: python track.py"""
from datetime import datetime, timedelta

from db import connect


def find_returns(window_days: int = 14, now: datetime | None = None) -> list[dict]:
    """Mark sent drafts as won back when the member's first check-in after sending falls
    inside the window. Returns details of the drafts newly marked. Safe to rerun."""
    now = now or datetime.now()
    db = connect()
    returned = []
    try:
        sent = db.execute(
            "SELECT d.id, d.member_id, d.sent_at, m.name FROM drafts d JOIN members m ON m.id = d.member_id"
            " WHERE d.status = 'sent' AND (d.won_back = 0 OR d.won_back IS NULL) AND d.sent_at IS NOT NULL"
        ).fetchall()
        for draft in sent:
            sent_at = datetime.fromisoformat(draft["sent_at"])
            window_end = min(sent_at + timedelta(days=window_days), now)
            checkins = [datetime.fromisoformat(r["ts"]) for r in
                        db.execute("SELECT ts FROM checkins WHERE member_id = ?", (draft["member_id"],))]
            after = [c for c in checkins if sent_at < c <= window_end]  # strictly after sending
            if not after:
                continue
            first = min(after)
            db.execute("UPDATE drafts SET won_back = 1, won_back_at = ? WHERE id = ?",
                       (first.isoformat(timespec="seconds"), draft["id"]))
            returned.append({"draft_id": draft["id"], "member_id": draft["member_id"], "name": draft["name"],
                             "days": round((first - sent_at).total_seconds() / 86400, 1)})
        db.commit()
    finally:
        db.close()
    return returned


def update_outcomes(window_days: int = 14, now: datetime | None = None) -> int:
    """Return how many drafts were newly marked as won back."""
    return len(find_returns(window_days, now))


if __name__ == "__main__":
    came_back = find_returns()
    print(f"Newly marked as won back: {len(came_back)}")
    for r in came_back:
        print(f"  {r['name']} (member {r['member_id']}, draft #{r['draft_id']}) came back after {r['days']} days")
