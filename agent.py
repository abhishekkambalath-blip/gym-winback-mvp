"""Win-back agent: the model calls tools to learn about a member, then drafts a message."""
import json
import os
import re
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError, field_validator

import rag
from datasource import get_datasource

load_dotenv()

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
SYSTEM_PROMPT_FILE = Path(__file__).parent / "prompts" / "winback_system.md"
MAX_TOOL_CALLS = 6
MAX_WORDS = 60
ANGLES = ["check_in", "free_pt", "pause_plan", "better_class_time", "payment_help"]
CHANNELS = ["whatsapp", "sms", "email"]


class AgentError(Exception):
    """Clear, user-facing error from the agent."""


def valid_offer_ids() -> list[str]:
    return [c["heading"] for c in rag.load_chunks()
            if c["source"] == "offers.md" and c["heading"].startswith("offer_")]


class Draft(BaseModel):
    message: str
    channel: str
    offer_id: str | None = None
    angle: str
    reasoning_for_staff: str

    @field_validator("angle")
    @classmethod
    def check_angle(cls, v):
        if v not in ANGLES:
            raise ValueError(f"angle must be one of {ANGLES}, got '{v}'")
        return v

    @field_validator("channel")
    @classmethod
    def check_channel(cls, v):
        if v not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}, got '{v}'")
        return v

    @field_validator("offer_id")
    @classmethod
    def check_offer(cls, v):
        if v is not None and v not in valid_offer_ids():
            raise ValueError(f"offer_id must be null or one of {valid_offer_ids()}, got '{v}'")
        return v

    @field_validator("message")
    @classmethod
    def check_length(cls, v):
        words = len(v.split())
        if words > MAX_WORDS:
            raise ValueError(f"message has {words} words, the maximum is {MAX_WORDS}")
        return v


# ---------- tools ----------

def branch_id(branch: str) -> str:
    b = branch.lower().strip()
    return "hsr" if b.startswith("hsr") else b


def get_member_profile(member_id: int) -> dict:
    ds = get_datasource()
    member = ds.get_member(member_id)
    if member is None:
        return {"error": f"No member with id {member_id}"}
    now = datetime.now()
    checkins = ds.get_checkins(member_id, since_days=70)
    failed = member.get("payment_failed_date")
    payment_failed = bool(failed) and 0 <= (now.date() - datetime.fromisoformat(failed).date()).days <= 30
    # Deliberately excludes phone number and full name.
    return {
        "first_name": member["name"].split()[0],
        "branch": member["branch"],
        "plan": member["plan"],
        "member_since": member["join_date"],
        "favourite_class": member["fav_class"],
        "usual_time": member["usual_time"],
        "last_checkin_date": max(checkins).date().isoformat() if checkins else None,
        "visits_last_10_weeks": len(checkins),
        "payment_failed_recently": payment_failed,
    }


def _sections(source: str, keep) -> str:
    return "\n\n".join(c["text"] for c in rag.load_chunks()
                      if c["source"] == source and keep(c["heading"]))


def get_branch_offers(branch_id_: str) -> str:
    b = branch_id(branch_id_)
    return _sections("offers.md", lambda h: h.startswith("offer_") or h == b)


def get_class_schedule(branch_id_: str) -> str:
    b = branch_id(branch_id_)
    return _sections("schedules.md", lambda h: h == b) or f"No schedule found for '{branch_id_}'."


def search_knowledge(query: str) -> list[dict]:
    return rag.search(query)


TOOL_FUNCTIONS = {
    "get_member_profile": lambda a: get_member_profile(a["member_id"]),
    "get_branch_offers": lambda a: get_branch_offers(a["branch_id"]),
    "get_class_schedule": lambda a: get_class_schedule(a["branch_id"]),
    "search_knowledge": lambda a: search_knowledge(a["query"]),
}

TOOLS = [
    {"name": "get_member_profile",
     "description": "Get a member's first name, branch, plan, join date, favourite class, usual time, last check-in, visits in the last 10 weeks and whether a payment failed recently.",
     "input_schema": {"type": "object", "properties": {"member_id": {"type": "integer"}}, "required": ["member_id"]}},
    {"name": "get_branch_offers",
     "description": "Get the offers every branch can use plus that branch's own news (new classes, times).",
     "input_schema": {"type": "object", "properties": {"branch_id": {"type": "string", "description": "koramangala, indiranagar or hsr"}}, "required": ["branch_id"]}},
    {"name": "get_class_schedule",
     "description": "Get the class types and time slots for one branch.",
     "input_schema": {"type": "object", "properties": {"branch_id": {"type": "string", "description": "koramangala, indiranagar or hsr"}}, "required": ["branch_id"]}},
    {"name": "search_knowledge",
     "description": "Search the policy, FAQ and past win-back messages. Use it for what staff may offer and which message styles worked.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


# ---------- agent loop ----------

def _run_tool(name: str, args: dict) -> str:
    try:
        return json.dumps(TOOL_FUNCTIONS[name](args))
    except Exception as e:  # report tool problems to the model instead of crashing
        return json.dumps({"error": f"{name} failed: {e}"})


def _call_model(client, messages: list, system: str):
    try:
        return client.messages.create(model=MODEL, max_tokens=1024, system=system,
                                      tools=TOOLS, messages=messages)
    except anthropic.APIError as e:
        raise AgentError(f"The Anthropic API call failed ({type(e).__name__}): {e}") from e


def _run_until_text(client, messages: list, system: str, state: dict) -> str:
    """Call the model while it asks for tools. Returns the final text."""
    for _ in range(MAX_TOOL_CALLS + 4):  # hard stop so a stubborn model cannot loop forever
        response = _call_model(client, messages, system)
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if state["calls"] >= MAX_TOOL_CALLS:
                output = "Tool call limit reached. Answer now with the final JSON object only."
            else:
                state["calls"] += 1
                output = _run_tool(block.name, block.input)
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        messages.append({"role": "user", "content": results})
    raise AgentError("The model kept asking for tools and never produced a draft.")


def _parse_draft(text: str) -> Draft:
    """Raise ValueError (or pydantic ValidationError, a ValueError) if text is not a valid Draft."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("the reply did not contain a JSON object")
    try:
        data = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"the reply was not valid JSON ({e})")
    return Draft(**data)


def draft_winback(member_id: int, reasons: list[str], client=None) -> Draft:
    if client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise AgentError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
        client = anthropic.Anthropic()

    system = SYSTEM_PROMPT_FILE.read_text()
    reason_text = "\n".join(f"- {r}" for r in reasons) or "- (none)"
    messages = [{"role": "user", "content":
                 f"Write a win-back message for member id {member_id}.\n\nWhy staff flagged them:\n{reason_text}"}]
    state = {"calls": 0}

    text = _run_until_text(client, messages, system, state)
    try:
        return _parse_draft(text)
    except (ValueError, ValidationError) as first_error:
        messages.append({"role": "user", "content":
                         f"Your reply was rejected: {first_error}. Reply again with corrected JSON only, no other text."})
        text = _run_until_text(client, messages, system, state)
        try:
            return _parse_draft(text)
        except (ValueError, ValidationError) as second_error:
            raise AgentError(f"The model returned an invalid draft twice. Last problem: {second_error}") from second_error
