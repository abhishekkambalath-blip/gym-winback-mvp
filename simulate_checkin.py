"""ONLY for demos and testing: inserts a fake check-in. Real check-ins will arrive
from the gym software. Usage: python simulate_checkin.py <member_id> [--at ISO_TIMESTAMP]"""
import argparse
import sys
from datetime import datetime

from db import connect


def simulate_checkin(member_id: int, at: datetime | None = None) -> str:
    """Insert one check-in and return its timestamp. Raises ValueError for unknown members."""
    ts = (at or datetime.now()).isoformat(sep=" ", timespec="seconds")
    db = connect()
    try:
        if db.execute("SELECT 1 FROM members WHERE id = ?", (member_id,)).fetchone() is None:
            raise ValueError(f"No member with id {member_id}")
        db.execute("INSERT INTO checkins (member_id, ts) VALUES (?, ?)", (member_id, ts))
        db.commit()
    finally:
        db.close()
    return ts


def main():
    p = argparse.ArgumentParser()
    p.add_argument("member_id", type=int)
    p.add_argument("--at", help="ISO timestamp, e.g. 2026-11-25T07:30 (default: now)")
    args = p.parse_args()
    try:
        at = datetime.fromisoformat(args.at) if args.at else None
        ts = simulate_checkin(args.member_id, at)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    print(f"Inserted a check-in for member {args.member_id} at {ts}")


if __name__ == "__main__":
    main()
