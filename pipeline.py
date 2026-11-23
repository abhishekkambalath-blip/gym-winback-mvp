"""The one shared path from scores to drafts, used by run_flow.py, the webhooks and the
nightly job:  score -> flag -> skip anyone contacted in the last 14 days -> draft -> save
as PENDING. It never sends anything; a human approves every draft in run_flow.py."""
import logging
import os
from dataclasses import dataclass, field
from types import SimpleNamespace

import agent
import drafts
from datasource import get_datasource
from scoring import score_all

log = logging.getLogger("pipeline")


def default_threshold() -> int:
    try:
        return int(os.getenv("RISK_THRESHOLD") or 50)
    except ValueError:
        return 50


def demo_draft(member: dict, reasons: list[str]) -> SimpleNamespace:
    """A simple template draft (no Claude) so the approval flow can be tried without an
    API key. Starts with [DEMO] so it can never go out through a real sender."""
    first, fav, time = member["name"].split()[0], member["fav_class"], member["usual_time"]
    text = " ".join(reasons)
    if "Asked to cancel" in text or "Asked to freeze" in text:
        # retention policy: offer a pause (or free PT) first, never argue
        angle, offer = "pause_plan", "offer_pause_30"
        body = (f"thanks for letting us know. Before anything changes, you can pause free for up to "
                f"30 days and come back to your {time} {fav} whenever you're ready. Want me to set that up?")
    elif "payment failed" in text:
        angle, offer = "payment_help", "offer_grace_7"
        body = (f"looks like your last payment didn't go through, probably a card hiccup. "
                f"No stress, you have a 7-day grace period so your {fav} sessions continue. "
                f"You can update the card in the app. Need a hand?")
    elif "New member" in text:
        angle, offer = "free_pt", "offer_pt_free"
        body = (f"we'd love to help you settle in. A free 30-minute session with a trainer can build "
                f"a simple plan around your {time} {fav}. Want me to book one this week?")
    elif "Membership ends" in text:
        angle, offer = "pause_plan", "offer_pause_30"
        body = (f"if things have been busy, you can pause free for up to 30 days and pick up your "
                f"{time} {fav} right where you left off. Want me to set that up?")
    else:
        angle, offer = "check_in", None
        body = f"we've missed you at {time} {fav}! There's always a spot for you. Shall I save you one this week?"
    return SimpleNamespace(message=f"[DEMO] Hi {first}, {body}", channel="whatsapp", offer_id=offer,
                           angle=angle, reasoning_for_staff=f"Template draft (no AI): chose {angle} from the flags.")


@dataclass
class PipelineResult:
    scored: int = 0
    flagged: int = 0
    drafted: int = 0
    skipped_recent: int = 0
    failed: int = 0
    draft_ids: list[int] = field(default_factory=list)


def run_pipeline(member_ids: list[int] | None = None, threshold: int | None = None, limit: int | None = None,
                 trigger: str = "manual", force: bool = False, placeholders: bool | None = None,
                 now=None) -> PipelineResult:
    """force=True bypasses the THRESHOLD only, never the 14-day rule.
    placeholders: template [DEMO] drafts instead of Claude (default: DEMO_DRAFTS=1 in the environment)."""
    threshold = default_threshold() if threshold is None else threshold
    if placeholders is None:
        placeholders = os.getenv("DEMO_DRAFTS") == "1"
    ds = get_datasource()
    scores = score_all(ds, now=now, member_ids=member_ids)
    flagged = [r for r in scores if force or r.score >= threshold]
    result = PipelineResult(scored=len(scores), flagged=len(flagged))

    attempts = 0
    for r in flagged:
        if limit is not None and attempts >= limit:
            break
        if drafts.recently_drafted(r.member_id):
            result.skipped_recent += 1
            if force:
                log.warning("Member %s needs human attention: a %s arrived but they were already contacted "
                            "in the last 14 days, so no new draft was made.", r.member_id, trigger)
            continue
        attempts += 1
        try:
            if placeholders:
                draft = demo_draft(ds.get_member(r.member_id), r.reasons)
            else:
                draft = agent.draft_winback(r.member_id, r.reasons)
            result.draft_ids.append(drafts.create_draft(r.member_id, r.score, r.reasons, draft, trigger=trigger))
            result.drafted += 1
        except Exception as e:  # one member failing must not stop the others; never log phone numbers
            result.failed += 1
            log.error("Drafting failed for member %s (%s): %s", r.member_id, type(e).__name__, e)
    return result
