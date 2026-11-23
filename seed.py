"""Create gym.db with fake members and check-ins. Safe to rerun (wipes tables)."""
import random
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta

from db import get_db_path

random.seed(42)
TODAY = date.today()

BRANCHES = ["Koramangala", "Indiranagar", "HSR Layout"]
PLANS = ["monthly", "quarterly", "annual"]
CLASSES = ["Spin", "HIIT", "Yoga", "Zumba", "Strength", "CrossFit"]
TIMES = {"morning": (6, 9), "lunch": (12, 14), "evening": (18, 21)}  # hour ranges
FIRST = ["Aarav", "Vivaan", "Arjun", "Rohan", "Karthik", "Rahul", "Siddharth", "Aditya",
         "Ananya", "Priya", "Sneha", "Divya", "Meera", "Kavya", "Ishita", "Pooja",
         "Suresh", "Ramesh", "Vikram", "Nikhil", "Deepa", "Lakshmi", "Neha", "Anjali"]
LAST = ["Sharma", "Reddy", "Nair", "Iyer", "Patel", "Gowda", "Rao", "Menon",
        "Singh", "Kumar", "Joshi", "Shetty", "Pillai", "Bhat", "Verma", "Das"]

SCHEMA = """
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS drafts;
DROP TABLE IF EXISTS checkins;
DROP TABLE IF EXISTS members;
CREATE TABLE members (
    id INTEGER PRIMARY KEY, name, branch, plan, phone, join_date, contract_end,
    fav_class, usual_time, payment_failed_date, status DEFAULT 'active'
);
CREATE TABLE checkins (id INTEGER PRIMARY KEY, member_id, ts);
CREATE INDEX idx_checkins_member_ts ON checkins (member_id, ts);
CREATE TABLE drafts (
    id INTEGER PRIMARY KEY, member_id, score, reasons, message,
    status DEFAULT 'pending', edited_message, reject_reason,
    created_at, sent_at, won_back DEFAULT 0
);
"""


def make_ts(day, usual_time):
    """Random check-in timestamp on `day` within the member's usual time window."""
    start, end = TIMES[usual_time]
    return datetime(day.year, day.month, day.day, random.randint(start, end - 1),
                    random.randint(0, 59)).strftime("%Y-%m-%d %H:%M")


def visit_days(start, end, prob):
    """Days in [start, end] (inclusive) on which the member visits, each with chance prob."""
    days, d = [], start
    while d <= end:
        if random.random() < prob:
            days.append(d)
        d += timedelta(days=1)
    return days


def main():
    db = sqlite3.connect(get_db_path())
    db.executescript(SCHEMA)

    n_churn = round(150 * 0.15)  # 22
    patterns = ["fading", "gone", "new_dropout"]
    # roughly equal split of churn patterns
    churn_labels = [patterns[i % 3] for i in range(n_churn)]
    churn_ids = set(random.sample(range(1, 151), n_churn))
    label_for = dict(zip(sorted(churn_ids), churn_labels))
    # ~40% of churn members get a payment/contract signal
    risky = set(random.sample(sorted(churn_ids), round(n_churn * 0.4)))

    pattern_count, branch_count, total_checkins = Counter(), Counter(), 0

    for mid in range(1, 151):
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"
        branch, plan = random.choice(BRANCHES), random.choice(PLANS)
        phone = f"+91 {random.choice('6789')}{random.randint(0, 9)}{random.randint(10000000, 99999999)}"
        fav, usual = random.choice(CLASSES), random.choice(list(TIMES))
        pattern = label_for.get(mid)

        if pattern == "new_dropout":
            join = TODAY - timedelta(days=random.randint(45, 80))
        else:
            join = TODAY - timedelta(days=random.randint(200, 900))

        # contract end and failed payment
        contract_end = TODAY + timedelta(days=random.randint(40, 300))
        payment_failed = None
        if mid in risky:
            if random.random() < 0.5:
                payment_failed = TODAY - timedelta(days=random.randint(1, 20))
            else:
                contract_end = TODAY + timedelta(days=random.randint(1, 28))

        db.execute(
            "INSERT INTO members (id, name, branch, plan, phone, join_date, contract_end,"
            " fav_class, usual_time, payment_failed_date) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, name, branch, plan, phone, join.isoformat(), contract_end.isoformat(),
             fav, usual, payment_failed.isoformat() if payment_failed else None))

        # check-ins over the last 6 months (~183 days), never before joining
        window_start = max(TODAY - timedelta(days=183), join)
        yesterday = TODAY - timedelta(days=1)
        normal = lambda a, b: visit_days(a, b, random.uniform(0.5, 0.6))

        if pattern == "fading":
            cut = TODAY - timedelta(days=28)
            days = normal(window_start, cut - timedelta(days=1)) + \
                visit_days(cut, yesterday, 0.07)
        elif pattern == "gone":
            days = normal(window_start, TODAY - timedelta(days=17))
        elif pattern == "new_dropout":
            days = normal(join, join + timedelta(days=21)) + \
                visit_days(join + timedelta(days=22), yesterday, 0.03)
        else:
            days = normal(window_start, yesterday)

        db.executemany("INSERT INTO checkins (member_id, ts) VALUES (?,?)",
                       [(mid, make_ts(d, usual)) for d in days])
        total_checkins += len(days)
        branch_count[branch] += 1
        pattern_count[pattern or "healthy"] += 1

    db.commit()
    db.close()

    print("Members per branch:")
    for b in BRANCHES:
        print(f"  {b}: {branch_count[b]}")
    print(f"Total check-ins: {total_checkins}")
    print("Churn patterns given:")
    for p in patterns:
        print(f"  {p}: {pattern_count[p]}")
    print(f"  healthy (no pattern): {pattern_count['healthy']}")
    print(f"Members with failed payment or contract ending soon: {len(risky)}")
    # Printout only; labels are NOT stored in the DB.
    print("Churn-pattern member ids:",
          {p: [m for m, l in label_for.items() if l == p] for p in patterns})


if __name__ == "__main__":
    main()
