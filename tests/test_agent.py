import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import agent  # noqa: E402

GOOD = {"message": "Hi Priya! Missed you at Yoga. A 7:30 PM batch just started at HSR. Want a spot Thursday?",
        "channel": "whatsapp", "offer_id": None, "angle": "better_class_time",
        "reasoning_for_staff": "She loves Yoga and the evening slot fits."}


def text(s):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=s)])


def tool(name="search_knowledge", args=None, id="t1"):
    block = SimpleNamespace(type="tool_use", name=name, id=id, input=args or {"query": "past winbacks"})
    return SimpleNamespace(stop_reason="tool_use", content=[block])


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.messages = self  # client.messages.create(...)

    def create(self, **kwargs):
        self.calls += 1
        return self.responses.pop(0)


def test_normal_run_with_tool_call():
    client = FakeClient([tool(), text(json.dumps(GOOD))])
    d = agent.draft_winback(1, ["No check-in for 20 days"], client=client)
    assert d.angle == "better_class_time" and d.offer_id is None
    assert client.calls == 2


def test_retry_after_invalid_json():
    client = FakeClient([text("Sure! Here you go"), text(json.dumps(GOOD))])
    assert agent.draft_winback(1, [], client=client).channel == "whatsapp"
    assert client.calls == 2


def test_invalid_twice_raises_clear_error():
    client = FakeClient([text("nope"), text("still nope")])
    with pytest.raises(agent.AgentError, match="invalid draft twice"):
        agent.draft_winback(1, [], client=client)


def test_unknown_offer_id_rejected():
    bad = {**GOOD, "offer_id": "offer_free_month"}
    with pytest.raises(agent.AgentError, match="offer_id"):
        agent.draft_winback(1, [], client=FakeClient([text(json.dumps(bad))] * 2))
    ok = {**GOOD, "offer_id": "offer_pt_free", "angle": "free_pt"}
    assert agent.draft_winback(1, [], client=FakeClient([text(json.dumps(ok))])).offer_id == "offer_pt_free"


def test_message_over_60_words_rejected():
    bad = {**GOOD, "message": " ".join(["word"] * 61)}
    with pytest.raises(agent.AgentError, match="61 words"):
        agent.draft_winback(1, [], client=FakeClient([text(json.dumps(bad))] * 2))


def test_bad_angle_rejected():
    bad = {**GOOD, "angle": "guilt_trip"}
    with pytest.raises(agent.AgentError, match="angle"):
        agent.draft_winback(1, [], client=FakeClient([text(json.dumps(bad))] * 2))


def test_tool_call_cap(monkeypatch):
    executed = []
    monkeypatch.setitem(agent.TOOL_FUNCTIONS, "search_knowledge", lambda a: executed.append(1) or [])
    client = FakeClient([tool(id=f"t{i}") for i in range(8)] + [text(json.dumps(GOOD))])
    # 8 tool requests, but only 6 may run; the loop's hard stop then allows 4 extra rounds.
    try:
        agent.draft_winback(1, [], client=client)
    except agent.AgentError:
        pass
    assert len(executed) == 6


def test_api_failure_is_clear_error():
    import anthropic

    class Boom:
        messages = None

        def __init__(self):
            self.messages = self

        def create(self, **kw):
            raise anthropic.APIConnectionError(request=SimpleNamespace())

    with pytest.raises(agent.AgentError, match="API call failed"):
        agent.draft_winback(1, [], client=Boom())


def test_profile_never_exposes_phone_or_full_name(monkeypatch):
    member = {"id": 5, "name": "Priya Sharma", "branch": "HSR Layout", "plan": "monthly",
              "phone": "+91 9812345678", "join_date": "2025-01-01", "contract_end": "2027-01-01",
              "fav_class": "Yoga", "usual_time": "evening",
              "payment_failed_date": (datetime.now() - timedelta(days=5)).date().isoformat()}

    class FakeDS:
        def get_member(self, mid):
            return member

        def get_checkins(self, mid, since_days=70):
            return [datetime.now() - timedelta(days=3)]

    monkeypatch.setattr(agent, "get_datasource", lambda: FakeDS())
    profile = agent.get_member_profile(5)
    dumped = json.dumps(profile)
    assert "9812345678" not in dumped and "Sharma" not in dumped and "phone" not in dumped
    assert profile["first_name"] == "Priya" and profile["payment_failed_recently"] is True
    assert profile["visits_last_10_weeks"] == 1


def test_missing_api_key_is_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(agent.AgentError, match="ANTHROPIC_API_KEY"):
        agent.draft_winback(1, [])
