"""One-command demo of the whole loop. Works without an API key.
Usage: python demo.py   (uses demo_gym.db, deleted at the start; gym.db is never touched)"""
import os
import sys
from datetime import datetime, timedelta

os.environ["GYM_DB"] = "demo_gym.db"  # must be set before anything opens the database
sys.stdout.reconfigure(line_buffering=True)

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import drafts  # noqa: E402
import feedback  # noqa: E402
import seed  # noqa: E402
import stats  # noqa: E402
import track  # noqa: E402
from datasource import get_datasource  # noqa: E402
from run_flow import demo_draft  # noqa: E402
from scoring import score_all  # noqa: E402
from sender import MockSender  # noqa: E402
from simulate_checkin import simulate_checkin  # noqa: E402

REVIEWER = "demo-script"


def header(n: int, title: str) -> None:
    print(f"\n{'=' * 70}\n{n}. {title}\n{'=' * 70}")


def main():
    if os.path.exists("demo_gym.db"):
        os.remove("demo_gym.db")

    header(1, "Seed fake data into demo_gym.db")
    seed.main()

    header(2, "Score members and draft messages for the top 5")
    ds = get_datasource()
    members = {m["id"]: m for m in ds.list_active_members()}
    top5 = score_all(ds)[:5]
    use_claude = bool(os.getenv("ANTHROPIC_API_KEY"))
    if use_claude:
        from agent import AgentError, draft_winback
        print("ANTHROPIC_API_KEY found: drafting with the real Claude agent.")
    else:
        print("NOTE: ANTHROPIC_API_KEY is not set, so these are template [DEMO] placeholder drafts, not Claude.")
    for r in top5:
        draft = None
        if use_claude:
            try:
                draft = draft_winback(r.member_id, r.reasons)
            except AgentError as e:
                print(f"  Agent failed for member {r.member_id} ({e}); using a placeholder instead.")
        draft = draft or demo_draft(members[r.member_id], r.reasons)
        did = drafts.create_draft(r.member_id, r.score, r.reasons, draft)
        print(f"  Draft #{did} for {members[r.member_id]['name']} (score {r.score}, angle {draft.angle}):")
        print(f"    {draft.message}")

    header(3, "Staff review: SCRIPTED stand-in for a human reviewer")
    print("(In real use a person decides each one with run_flow.py. Here a script does it so the demo runs alone.)")
    pending = drafts.list_pending()
    sender = MockSender()
    for d in pending[:2]:
        print(f"\n  APPROVE draft #{d['id']} ({d['member_name']}):")
        drafts.approve_and_send(d["id"], REVIEWER, sender=sender)
    d = pending[2]
    edited = d["message"] + " See you soon!"
    print(f"\n  EDIT then approve draft #{d['id']} ({d['member_name']}): appended 'See you soon!'")
    drafts.approve_and_send(d["id"], REVIEWER, final_message=edited, sender=sender)
    d = pending[3]
    print(f"\n  REJECT draft #{d['id']} ({d['member_name']}): reason 'demo: not a good time'")
    drafts.reject(d["id"], REVIEWER, "demo: not a good time")
    print(f"\n  LEAVE PENDING draft #{pending[4]['id']} ({pending[4]['member_name']})")

    header(4, "Track outcomes: two members check in again")
    sent = [drafts.get_draft(d["id"]) for d in (pending[0], pending[2])]
    latest = now = datetime.now()
    for d, days in zip(sent, (3, 5)):
        at = datetime.fromisoformat(d["sent_at"]) + timedelta(days=days)
        simulate_checkin(d["member_id"], at)
        print(f"  Simulated check-in: {d['member_name']} at {at:%Y-%m-%d %H:%M} ({days} days after the message)")
        latest = max(latest, at)
    now = latest + timedelta(minutes=1)  # pretend time has moved forward to just after the last visit
    print(f"  Running update_outcomes as if it is now {now:%Y-%m-%d %H:%M}...")
    for r in track.find_returns(now=now):
        print(f"  WON BACK: {r['name']} (draft #{r['draft_id']}) after {r['days']} days")
    print(f"  Running it again changes: {track.update_outcomes(now=now)} (idempotent)")

    header(5, "Stats")
    print(stats.report(now=now))

    header(6, "Feedback loop: what would be added to knowledge/past_winbacks.md (dry run)")
    sections = feedback.export_learnings(write=False, now=now)
    for s in sections:
        print(s)
    print(f"{len(sections)} section(s) would be added. Nothing was written.")
    if not use_claude:
        print("These are [DEMO] placeholders, so --write would skip them; real messages would be added.")

    header(7, "Summary")
    print(f"""- Seeded 150 fake members into demo_gym.db and scored them; the top 5 got drafts
  ({'written by Claude' if use_claude else 'template placeholders, because no API key is set'}).
- A scripted reviewer approved 2, edited and approved 1, rejected 1 and left 1 pending.
  Only the 3 approved drafts were sent, all through drafts.approve_and_send with the mock sender.
- 2 of the 3 messaged members checked in afterwards (3 and 5 days later) and were marked as won back,
  so the win-back rate is 2 of 3 sent. The third is still inside its 14-day window.
- The feedback step showed which outcomes would become new examples for the agent to learn from.
- gym.db was not touched. Delete demo_gym.db any time.""")


if __name__ == "__main__":
    main()
