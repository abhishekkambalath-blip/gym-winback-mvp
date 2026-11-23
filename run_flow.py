"""Generate win-back drafts for at-risk members, then let staff review them.
Nothing is sent without a human pressing 'a' (approve) or confirming an edit."""
import argparse
import logging

import drafts
from datasource import get_datasource
from pipeline import demo_draft, run_pipeline  # noqa: F401  (demo_draft re-exported for demo.py)
from scoring import level_for, score_all

MAX_WORDS = 60


def generate(args) -> None:
    """Stage A. Uses the shared pipeline, so webhooks, nightly and this CLI follow one code path."""
    ds = get_datasource()
    ids = None
    if args.branch:
        ids = [m["id"] for m in ds.list_active_members() if m["branch"].lower().startswith(args.branch)]
    if args.demo_drafts:
        # exactly the top 3 at-risk members, so reruns do not add more
        top = [r for r in score_all(ds, member_ids=ids) if r.score >= args.threshold][:3]
        result = run_pipeline([r.member_id for r in top], threshold=args.threshold, placeholders=True)
    else:
        result = run_pipeline(ids, threshold=args.threshold, limit=args.limit, placeholders=False)
    print(f"Generated {result.drafted}, skipped {result.skipped_recent} (recently drafted), failed {result.failed}")


def print_card(d: dict) -> None:
    words = len(d["message"].split())
    print("\n" + "=" * 70)
    print(f"Draft #{d['id']}: {d['member_name']} ({d['branch']})")
    print(f"Risk: score {d['score']}, {level_for(d['score'])}")
    for reason in d["reasons"]:
        print(f"  - {reason}")
    print(f"\nMessage ({words} words):\n  {d['message']}")
    print(f"Angle: {d['angle']}   Offer: {d['offer_id']}")
    print(f"Why: {d['reasoning']}")


def ask(prompt: str, valid: str) -> str:
    while True:
        answer = input(prompt).strip().lower()
        if answer and answer in valid:
            return answer
        print(f"Please enter one of: {', '.join(valid)}")


def edit_message(d: dict) -> str | None:
    """Return the confirmed replacement text, or None if staff backed out."""
    while True:
        text = input("Type the replacement message (blank to go back): ").strip()
        if not text:
            return None
        words = len(text.split())
        if words > MAX_WORDS:
            print(f"Warning: {words} words (limit is {MAX_WORDS}). You can still send it.")
        print(f"\nYour message:\n  {text}")
        if ask("Send this? [y/n]: ", "yn") == "y":
            return text


def review(branch: str | None, reviewer: str, sender=None) -> dict:
    counts = {"sent": 0, "edited": 0, "rejected": 0, "skipped": 0}
    pending = drafts.list_pending(branch)
    print(f"\n{len(pending)} pending draft(s) to review.")
    try:
        for d in pending:
            print_card(d)
            while True:
                choice = ask("\n[a]pprove and send / [e]dit / [r]eject / [s]kip / [q]uit: ", "aersq")
                try:
                    if choice == "a":
                        drafts.approve_and_send(d["id"], reviewer, sender=sender)
                        counts["sent"] += 1
                    elif choice == "e":
                        text = edit_message(d)
                        if text is None:
                            continue
                        drafts.approve_and_send(d["id"], reviewer, final_message=text, sender=sender)
                        counts["edited"] += 1
                    elif choice == "r":
                        reason = ""
                        while not reason:
                            reason = input("Reason for rejecting (required): ").strip()
                        drafts.reject(d["id"], reviewer, reason)
                        counts["rejected"] += 1
                    elif choice == "s":
                        counts["skipped"] += 1
                    else:  # q
                        return counts
                except Exception as e:  # e.g. sender failure: leave pending, move on
                    print(f"Could not complete this draft, it stays pending: {e}")
                    counts["skipped"] += 1
                break
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Remaining drafts stay pending.")
    return counts


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--threshold", type=int, default=50)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--branch", choices=["koramangala", "indiranagar", "hsr"])
    p.add_argument("--reviewer", default="staff")
    p.add_argument("--generate-only", action="store_true")
    p.add_argument("--review-only", action="store_true")
    p.add_argument("--demo-drafts", action="store_true")
    args = p.parse_args()
    if args.generate_only and args.review_only:
        p.error("--generate-only and --review-only cannot be used together")

    if not args.review_only:
        generate(args)
    if not args.generate_only:
        c = review(args.branch, args.reviewer)
        print(f"\nSummary: sent {c['sent']}, edited and sent {c['edited']}, "
              f"rejected {c['rejected']}, skipped {c['skipped']}")


if __name__ == "__main__":
    main()
