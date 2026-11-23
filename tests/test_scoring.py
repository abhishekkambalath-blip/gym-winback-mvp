import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from scoring import score_member  # noqa: E402

NOW = datetime(2026, 11, 23, 12, 0)
OLD_JOIN = "2025-01-01"


def member(**kw):
    base = {"id": 1, "join_date": OLD_JOIN, "contract_end": "2027-06-01", "payment_failed_date": None}
    return {**base, **kw}


def days_ago(n):
    return NOW - timedelta(days=n)


def visits(days_list):
    return [days_ago(d) for d in sorted(days_list, reverse=True)]


HEALTHY = visits(range(1, 71, 2))  # a visit every other day


def test_healthy_member():
    r = score_member(member(), HEALTHY, NOW)
    assert r.score == 0 and r.reasons == [] and r.level == "low"


def test_big_visit_drop():
    # daily for ~56 days, then only 3 visits in the last 14 days (last one yesterday)
    checkins = visits(list(range(15, 71)) + [1, 5, 9])
    r = score_member(member(), checkins, NOW)
    assert r.score == 30
    assert "Visits dropped from" in r.reasons[0]


def test_medium_drop():
    # baseline 14/14 days, recent 9 visits -> drop ~36%
    checkins = visits(list(range(15, 71)) + list(range(1, 10)))
    r = score_member(member(), checkins, NOW)
    assert r.score == 15
    assert "down 36%" in r.reasons[0]


def test_12_days_no_visit():
    r = score_member(member(), visits([12]), NOW)  # sparse history: no drop signal
    assert r.score == 25
    assert r.reasons == ["No check-in for 12 days"]


def test_25_days_no_visit():
    r = score_member(member(), visits([25]), NOW)
    assert r.score == 35
    assert r.reasons == ["No check-in for 25 days"]


def test_zero_checkins():
    r = score_member(member(), [], NOW)
    assert r.score == 35
    assert r.reasons == ["No check-ins recorded in the last 10 weeks"]


def test_payment_failed():
    recent = (NOW - timedelta(days=10)).date().isoformat()
    old = (NOW - timedelta(days=40)).date().isoformat()
    assert score_member(member(payment_failed_date=recent), HEALTHY, NOW).score == 20
    assert score_member(member(payment_failed_date=old), HEALTHY, NOW).score == 0


def test_contract_end():
    d = lambda n: (NOW + timedelta(days=n)).date().isoformat()
    r = score_member(member(contract_end=d(10)), HEALTHY, NOW)
    assert r.score == 15 and r.reasons == ["Membership ends in 10 days"]
    assert score_member(member(contract_end=d(60)), HEALTHY, NOW).score == 0
    assert score_member(member(contract_end=d(-5)), HEALTHY, NOW).score == 0


def test_new_member_dropout():
    join = (NOW - timedelta(days=60)).date().isoformat()
    checkins = visits(range(39, 61))  # active ~3 weeks, then nothing
    r = score_member(member(join_date=join), checkins, NOW)
    assert any(x.startswith("New member") for x in r.reasons)
    assert r.score >= 45


def test_very_new_member_has_no_drop_reason():
    join = (NOW - timedelta(days=10)).date().isoformat()
    r = score_member(member(join_date=join), visits(range(1, 11)), NOW)
    assert not any("Visits" in x for x in r.reasons)


def test_score_capped_at_100():
    join = (NOW - timedelta(days=60)).date().isoformat()
    m = member(join_date=join, payment_failed_date=(NOW - timedelta(days=3)).date().isoformat(),
               contract_end=(NOW + timedelta(days=5)).date().isoformat())
    r = score_member(m, visits(range(39, 61)), NOW)
    assert r.score == 100


# ---------- Step 6: freeze / cancellation signals ----------

def test_freeze_signal_adds_25():
    r = score_member(member(), HEALTHY, NOW, signals=["freeze_request"])
    assert r.score == 25 and r.reasons == ["Asked to freeze their membership"]


def test_cancellation_signal_adds_40():
    r = score_member(member(), HEALTHY, NOW, signals=["cancellation_request"])
    assert r.score == 40 and r.reasons == ["Asked to cancel their membership"] and r.level == "medium"


def test_signals_still_capped_at_100():
    r = score_member(member(), [], NOW, signals=["cancellation_request", "freeze_request"])
    assert r.score == 100  # 35 + 40 + 25 = 100
    r = score_member(member(payment_failed_date=(NOW - timedelta(days=3)).date().isoformat()), [], NOW,
                     signals=["cancellation_request", "freeze_request"])
    assert r.score == 100


def test_score_all_picks_up_stored_events(tmp_db):
    from datasource import get_datasource
    from db import connect
    from scoring import score_all

    now = datetime.now()
    db = connect()
    db.executemany("INSERT INTO events (event_id, type, member_id, event_ts) VALUES (?,?,?,?)",
                   [("e1", "cancellation_request", 1, now.isoformat(timespec="seconds")),
                    ("e2", "freeze_request", 2, now.isoformat(timespec="seconds")),
                    ("e3", "cancellation_request", 3, (now - timedelta(days=40)).isoformat(timespec="seconds"))])
    db.commit()
    db.close()
    results = {r.member_id: r for r in score_all(get_datasource())}
    assert "Asked to cancel their membership" in results[1].reasons
    assert "Asked to freeze their membership" in results[2].reasons
    assert not any("Asked to" in x for x in results[3].reasons)  # older than 30 days
