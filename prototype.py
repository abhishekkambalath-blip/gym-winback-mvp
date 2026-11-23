"""One-command walkthrough of everything built so far (Steps 1-4).

Usage:  python prototype.py            (pauses between steps)
        python prototype.py --no-pause (runs straight through to the review)

It uses its own database, prototype.db, rebuilt on every run, so gym.db is never
touched. With ANTHROPIC_API_KEY in .env the drafts come from the real Claude agent;
without it, template [DEMO] drafts are used so you can still try the approval flow.
"""
import argparse
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(line_buffering=True)  # keep step headers in order with the scripts' output
ENV ={**os.environ, "GYM_DB": "prototype.db"}
os.environ["GYM_DB"] = "prototype.db"  # for the in-process steps below
PY = sys.executable


def step(n: int, title: str, pause: bool) -> None:
    if pause and n > 1:
        input("\nPress Enter for the next step...")
    print(f"\n{'#' * 70}\n# Step {n}: {title}\n{'#' * 70}")


def run(*args: str) -> None:
    subprocess.run([PY, *args], env=ENV, check=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--no-pause", action="store_true")
    pause = not p.parse_args().no_pause
    has_key = bool(os.getenv("ANTHROPIC_API_KEY"))

    step(1, "Seed fake data (150 members, 6 months of check-ins)", pause)
    run("seed.py")

    from datasource import get_datasource
    from scoring import score_all
    top = score_all(get_datasource())

    step(2, "Spot-check two members: a healthy one, then the riskiest one", pause)
    run("check_data.py", "1")
    print()
    run("check_data.py", str(top[0].member_id))

    step(3, "Churn scoring (top 5 at threshold 50)", pause)
    run("score_all.py", "--top", "5")

    step(4, "Knowledge search (RAG) and what the agent is allowed to see", pause)
    import agent
    import rag
    for query in ("payment failed", "member wants to cancel"):
        print(f"\nSearch: '{query}'")
        for hit in rag.search(query, k=3):
            print(f"  {hit['relevance']:.2f}  {hit['source']:<20} {hit['text'].splitlines()[0]}")
    print(f"\nget_member_profile({top[0].member_id})  <- no phone, no surname:")
    for key, value in agent.get_member_profile(top[0].member_id).items():
        print(f"  {key}: {value}")

    if has_key:
        step(5, "Draft win-back messages with the Claude agent (3 members)", pause)
        run("run_flow.py", "--generate-only", "--limit", "3")
    else:
        step(5, "Draft win-back messages (no API key: template [DEMO] drafts)", pause)
        print("ANTHROPIC_API_KEY is not set in .env, so Claude is skipped.")
        run("run_flow.py", "--generate-only", "--demo-drafts")

    step(6, "Staff review: you approve, edit, reject or skip each draft", pause)
    print("Approving uses the mock sender: it only prints 'SENT via ...', nothing really goes out.")
    run("run_flow.py", "--review-only")

    step(7, "What got recorded", False)
    run("show_drafts.py")
    print("\nDone. Rerun any time with: python prototype.py")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except subprocess.CalledProcessError as e:
        sys.exit(f"\nA step failed: {' '.join(e.cmd[1:])}")
