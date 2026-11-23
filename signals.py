"""Open member requests (freeze / cancellation) from the events table, for scoring."""
from datetime import datetime, timedelta

from db import connect

SIGNAL_TYPES = ("freeze_request", "cancellation_request")


def get_open_signals(member_id: int, now: datetime | None = None, days: int = 30) -> list[str]:
    """Distinct freeze_request / cancellation_request types received in the last `days` days."""
    cutoff = (now or datetime.now()) - timedelta(days=days)
    db = connect()
    try:
        rows = db.execute("SELECT type, event_ts FROM events WHERE member_id = ? AND type IN (?, ?)",
                          (member_id, *SIGNAL_TYPES)).fetchall()
    finally:
        db.close()
    return sorted({r["type"] for r in rows if r["event_ts"] and datetime.fromisoformat(r["event_ts"]) >= cutoff})
