"""Usage: python check_data.py <member_id>
Prints a member's profile and weekly check-in counts for the last 12 weeks."""
import sqlite3
import sys
from datetime import date, timedelta

from db import get_db_path


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        sys.exit("Usage: python check_data.py <member_id>")
    member_id = int(sys.argv[1])

    db = sqlite3.connect(get_db_path())
    db.row_factory = sqlite3.Row
    member = db.execute("SELECT * FROM members WHERE id = ?", (member_id,)).fetchone()
    if member is None:
        sys.exit(f"No member with id {member_id}")

    print("Profile")
    for key in member.keys():
        print(f"  {key}: {member[key]}")

    print("\nCheck-ins per week (last 12 weeks, oldest first)")
    today = date.today()
    for w in range(11, -1, -1):
        end = today - timedelta(days=7 * w)           # exclusive end
        start = end - timedelta(days=7)
        count = db.execute(
            "SELECT COUNT(*) FROM checkins WHERE member_id = ? AND ts >= ? AND ts < ?",
            (member_id, start.isoformat(), end.isoformat())).fetchone()[0]
        print(f"  {start} to {end - timedelta(days=1)}: {count:2d} {'#' * count}")


if __name__ == "__main__":
    main()
