"""Score all active members and print the riskiest ones.
Usage: python score_all.py [--top 15] [--threshold 50]"""
import argparse
from collections import Counter

from datasource import get_datasource
from scoring import score_all


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument("--threshold", type=int, default=50)
    args = parser.parse_args()

    ds = get_datasource()
    results = score_all(ds)
    members = {m["id"]: m for m in ds.list_active_members()}

    above = sum(1 for r in results if r.score >= args.threshold)
    levels = Counter(r.level for r in results)
    print(f"Scored {len(results)} members; {above} at or above threshold {args.threshold}")
    print(f"Risk levels: high={levels['high']}, medium={levels['medium']}, low={levels['low']}")
    print(f"\nTop {args.top}:")
    for r in results[:args.top]:
        m = members[r.member_id]
        print(f"#{r.member_id} {m['name']} ({m['branch']}) - score {r.score}, {r.level}")
        for reason in r.reasons:
            print(f"    - {reason}")


if __name__ == "__main__":
    main()
