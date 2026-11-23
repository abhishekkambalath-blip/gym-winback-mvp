"""Score members, then draft win-back messages for the top 3. Needs ANTHROPIC_API_KEY in .env."""
from agent import AgentError, draft_winback
from datasource import get_datasource
from scoring import score_all


def main():
    ds = get_datasource()
    for result in score_all(ds)[:3]:
        member = ds.get_member(result.member_id)
        print("=" * 70)
        print(f"{member['name']} ({member['branch']}) - score {result.score}, {result.level}")
        for reason in result.reasons:
            print(f"  flag: {reason}")
        try:
            d = draft_winback(result.member_id, result.reasons)
        except AgentError as e:
            print(f"  ERROR: {e}")
            continue
        print(f"\n  Message ({len(d.message.split())} words, {d.channel}):\n  {d.message}")
        print(f"  angle: {d.angle}\n  offer_id: {d.offer_id}\n  for staff: {d.reasoning_for_staff}")


if __name__ == "__main__":
    main()
