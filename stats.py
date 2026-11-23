"""Win-back stats. Usage: python stats.py [--branch koramangala|indiranagar|hsr]"""
import argparse
from collections import Counter
from datetime import datetime, timedelta

from db import connect

BRANCHES = ["Koramangala", "Indiranagar", "HSR Layout"]
ANGLES = ["check_in", "free_pt", "pause_plan", "better_class_time", "payment_help"]
WINDOW_DAYS = 14


def pct(part: int, whole: int) -> str:
    return "n/a" if not whole else f"{part / whole * 100:.0f}%"


def load_rows(branch: str | None = None) -> list[dict]:
    sql = "SELECT d.*, m.branch AS branch FROM drafts d JOIN members m ON m.id = d.member_id"
    params = []
    if branch:
        sql += " WHERE LOWER(m.branch) LIKE ?"
        params.append(branch.lower() + "%")
    db = connect()
    try:
        return [dict(r) for r in db.execute(sql, params)]
    finally:
        db.close()


def summarize(rows: list[dict], now: datetime | None = None) -> dict:
    now = now or datetime.now()
    cutoff = now - timedelta(days=WINDOW_DAYS)
    sent = [r for r in rows if r["status"] == "sent"]
    won = [r for r in sent if r.get("won_back")]
    waiting = [r for r in sent if not r.get("won_back")]
    in_window = [r for r in waiting if r.get("sent_at") and datetime.fromisoformat(r["sent_at"]) > cutoff]
    edited = [r for r in sent if r.get("edited_message")]
    return {
        "generated": len(rows),
        "pending": sum(r["status"] == "pending" for r in rows),
        "sent": len(sent),
        "rejected": sum(r["status"] == "rejected" for r in rows),
        "edited": len(edited), "edited_rate": pct(len(edited), len(sent)),
        "won_back": len(won), "win_rate": pct(len(won), len(sent)),
        "outcome_pending": len(in_window),
        "not_returned": len(waiting) - len(in_window),
    }


def by_angle(rows: list[dict]) -> list[tuple]:
    out = []
    for angle in ANGLES:
        sent = [r for r in rows if r["status"] == "sent" and r.get("angle") == angle]
        won = sum(1 for r in sent if r.get("won_back"))
        out.append((angle, len(sent), won, pct(won, len(sent))))
    return out


def top_reasons(rows: list[dict], n: int = 5) -> list[tuple]:
    return Counter(r["reject_reason"].strip().lower() for r in rows
                   if r["status"] == "rejected" and r.get("reject_reason")).most_common(n)


def report(branch: str | None = None, now: datetime | None = None) -> str:
    rows = load_rows(branch)
    groups = [b for b in BRANCHES if not branch or b.lower().startswith(branch.lower())]
    cols = ["generated", "pending", "sent", "rejected", "edited", "edited_rate",
            "won_back", "win_rate", "outcome_pending", "not_returned"]
    heads = ["drafts", "pending", "sent", "rejected", "edited", "edit %",
             "won back", "win %", "outcome pending", "not returned"]
    lines = ["Overview (window: %d days)" % WINDOW_DAYS,
             f"{'branch':<13}" + "".join(f"{h:>{max(len(h), 5) + 2}}" for h in heads)]
    for name, subset in [(g, [r for r in rows if r["branch"] == g]) for g in groups] + [("TOTAL", rows)]:
        s = summarize(subset, now)
        lines.append(f"{name:<13}" + "".join(f"{str(s[c]):>{max(len(h), 5) + 2}}" for c, h in zip(cols, heads)))

    lines += ["", "By angle", f"{'angle':<20}{'sent':>6}{'won back':>10}{'rate':>7}"]
    lines += [f"{a:<20}{s:>6}{w:>10}{r:>7}" for a, s, w, r in by_angle(rows)]

    lines += ["", "Top rejection reasons"]
    reasons = top_reasons(rows)
    lines += [f"{count:>4}  {reason}" for reason, count in reasons] or ["   (none)"]
    return "\n".join(lines)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--branch", choices=["koramangala", "indiranagar", "hsr"])
    print(report(p.parse_args().branch))
