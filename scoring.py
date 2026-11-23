"""Churn scoring. score_member is a pure function (no database access), so it can
be swapped for an ML model later with the same signature."""
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from signals import get_open_signals

WEIGHTS = json.loads((Path(__file__).parent / "scoring_weights.json").read_text())


@dataclass
class ScoreResult:
    member_id: int
    score: int
    level: str
    reasons: list[str]


def level_for(score: int) -> str:
    return "high" if score >= WEIGHTS["level_high"] else "medium" if score >= WEIGHTS["level_medium"] else "low"


def _visit_drop(join: date, checkins: list[datetime], now: datetime) -> tuple[float, float, float] | None:
    """Return (drop, baseline visits/week, recent visits/week), or None if not measurable."""
    recent_start = now - timedelta(days=14)
    baseline_start = max(datetime.combine(join, datetime.min.time()), now - timedelta(days=70))
    baseline_days = (recent_start - baseline_start).total_seconds() / 86400
    if baseline_days < 14:
        return None  # member too new
    baseline_visits = sum(1 for c in checkins if baseline_start <= c < recent_start)
    baseline_per_14 = baseline_visits / (baseline_days / 14)
    if baseline_per_14 < 3:
        return None  # too sparse to mean anything
    recent_visits = sum(1 for c in checkins if recent_start <= c <= now)
    drop = 1 - recent_visits / baseline_per_14
    return drop, baseline_per_14 / 2, recent_visits / 2


def score_member(member: dict, checkins: list[datetime], now: datetime | None = None,
                 signals: list[str] | None = None) -> ScoreResult:
    """Pure function. signals: events the member raised, e.g. ["cancellation_request"]."""
    now = now or datetime.now()
    today = now.date()
    w = WEIGHTS
    score, reasons = 0, []
    signals = signals or []

    # 0. Requests the member made themselves (strongest signals, listed first)
    if "cancellation_request" in signals:
        score += w["cancellation_request"]
        reasons.append("Asked to cancel their membership")
    if "freeze_request" in signals:
        score += w["freeze_request"]
        reasons.append("Asked to freeze their membership")
    join = date.fromisoformat(member["join_date"])

    # 1. Visit drop versus the member's own history
    result = _visit_drop(join, checkins, now)
    drop = result[0] if result else 0.0
    if result and drop > 0.5:
        score += w["visit_drop_high"]
        reasons.append(f"Visits dropped from {result[1]:.1f}/week to {result[2]:.1f}/week over the last 14 days")
    elif result and drop > 0.3:
        score += w["visit_drop_medium"]
        reasons.append(f"Visits are down {round(drop * 100)}% compared with their own average")

    # 2. Days since last check-in
    past = [c for c in checkins if c <= now]
    if not past:
        score += w["no_checkin_10_days"] + w["no_checkin_21_days_extra"]
        reasons.append("No check-ins recorded in the last 10 weeks")
    else:
        days = (now - max(past)).days
        if days > 21:
            score += w["no_checkin_10_days"] + w["no_checkin_21_days_extra"]
            reasons.append(f"No check-in for {days} days")
        elif days > 10:
            score += w["no_checkin_10_days"]
            reasons.append(f"No check-in for {days} days")

    # 3. Failed payment
    if member.get("payment_failed_date"):
        ago = (today - date.fromisoformat(member["payment_failed_date"])).days
        if 0 <= ago <= 30:
            score += w["payment_failed_30_days"]
            reasons.append("A payment failed in the last 30 days")

    # 4. Contract ending soon (not already ended)
    if member.get("contract_end"):
        left = (date.fromisoformat(member["contract_end"]) - today).days
        if 0 <= left <= 30:
            score += w["contract_ends_30_days"]
            reasons.append(f"Membership ends in {left} days")

    # 5. New member who is tailing off
    days_in = (today - join).days
    if days_in < 90 and drop > 0.3:
        score += w["new_member_declining"]
        reasons.append(f"New member ({days_in} days in) whose visits are tailing off")

    score = min(score, 100)
    return ScoreResult(member["id"], score, level_for(score), reasons)


def score_all(ds, now: datetime | None = None, member_ids: list[int] | None = None) -> list[ScoreResult]:
    """Score every active member (or only member_ids), highest score first.
    Open freeze/cancellation requests from the events table are passed in as signals."""
    if member_ids is None:
        members = ds.list_active_members()
    else:
        members = [m for m in map(ds.get_member, member_ids) if m and m.get("status", "active") == "active"]
    results = [score_member(m, ds.get_checkins(m["id"]), now, get_open_signals(m["id"], now)) for m in members]
    return sorted(results, key=lambda r: r.score, reverse=True)
