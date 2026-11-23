"""Send one test event to the webhook server.
Usage: python send_test_event.py <type> <member_id> [--url http://localhost:8000]"""
import argparse
import os
import sys
import uuid
from datetime import date, timedelta

import httpx
from dotenv import load_dotenv

TYPES = ["check_in", "payment_failed", "freeze_request", "cancellation_request", "membership_renewed"]


def main():
    load_dotenv()
    p = argparse.ArgumentParser()
    p.add_argument("type", choices=TYPES)
    p.add_argument("member_id", type=int)
    p.add_argument("--url", default="http://localhost:8000")
    args = p.parse_args()

    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not secret:
        sys.exit("WEBHOOK_SECRET is not set in .env")
    payload = {}
    if args.type == "membership_renewed":
        payload["new_contract_end"] = (date.today() + timedelta(days=90)).isoformat()
    body = {"event_id": f"test-{uuid.uuid4()}", "type": args.type, "member_id": args.member_id, "payload": payload}

    try:
        r = httpx.post(f"{args.url.rstrip('/')}/webhooks/gym-events", json=body,
                       headers={"X-Webhook-Secret": secret}, timeout=10)
    except httpx.HTTPError as e:
        sys.exit(f"Could not reach the server at {args.url}: {e}")
    print(f"{r.status_code} {r.text}")


if __name__ == "__main__":
    main()
