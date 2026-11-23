"""Print every draft as a table. Usage: python show_drafts.py [--status pending|sent|rejected]"""
import argparse
import textwrap
from itertools import zip_longest

from drafts import list_all


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--status")
    rows = list_all(p.parse_args().status)
    print(f"{'id':>3}  {'member':<18} {'branch':<12} {'status':<9} {'angle':<18} {'reviewer':<12} {'trigger':<21} edited")
    for d in rows:
        edited = bool(d["edited_message"])
        print(f"{d['id']:>3}  {d['member_name']:<18} {d['branch']:<12} {d['status']:<9} "
              f"{d['angle'] or '-':<18} {d['reviewer'] or '-':<12} {d.get('trigger') or '-':<21} {'yes' if edited else 'no'}")
        if edited:
            left = textwrap.wrap("ORIGINAL: " + d["message"], 36)
            right = textwrap.wrap("EDITED: " + d["edited_message"], 36)
            for a, b in zip_longest(left, right, fillvalue=""):
                print(f"       {a:<38}| {b}")
        if d["reject_reason"]:
            print(f"       rejected because: {d['reject_reason']}")
    print(f"\n{len(rows)} draft(s)")


if __name__ == "__main__":
    main()
