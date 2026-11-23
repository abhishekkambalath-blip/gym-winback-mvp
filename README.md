# Gym win-back MVP

Finds gym members who are likely to quit, drafts a personal win-back message for each
with Claude, and lets staff approve, edit or reject every message before it is sent.
It then tracks who came back and feeds those results into the examples the agent
learns from. Nothing is sent without a human approving it.

## Dashboard (start here)

```bash
./start.sh
```

Then open http://127.0.0.1:8000 and sign in with the `WEBHOOK_SECRET` value from `.env`
(`grep WEBHOOK_SECRET .env`) and your name. The first run creates `.env`, the access key and
`gym.db` for you. The dashboard has four tabs: **Review queue** (edit, approve or reject each
draft), **At risk** (draft a message for anyone), **Results** (win-back rate, what worked) and
**Activity** (every draft and what happened). Open **Demo tools** at the bottom to fake gym
events and watch the whole flow.

It shows "practice mode" until you plug in a real sender: approving prints the message in the
terminal and nothing is delivered. Without `ANTHROPIC_API_KEY` the drafts are `[DEMO]` templates.

## How it flows

```
trigger            webhook event, nightly job, or you run a script
   -> data pull        datasource.py      members and check-ins (gym.db today)
   -> scoring          scoring.py         risk score + plain-English reasons
   -> agent            agent.py + rag.py  Claude drafts a message using knowledge/
   -> staff approval   run_flow.py        approve / edit / reject / skip
   -> send             sender.py          mock sender today (prints only)
   -> track            track.py           did they check in within 14 days?
   -> feedback         feedback.py        outcomes become new examples in knowledge/
```

## Setup

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

`.env` variables:

| Variable | Needed? | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | for real drafts | Your Anthropic key. Without it, use `--demo-drafts`. |
| `ANTHROPIC_MODEL` | optional | Defaults to `claude-sonnet-5-5`. |
| `GYM_DB` | optional | Database file. Defaults to `gym.db`. |
| `WEBHOOK_SECRET` | for the server | Shared secret webhook callers must send. The server will not start without it. |
| `NIGHTLY_HOUR` | optional | Hour of the nightly job (default `2`). |
| `NIGHTLY_LIMIT` | optional | Max drafts per night, to cap API cost (default `20`). |
| `RISK_THRESHOLD` | optional | Score needed to get a draft (default `50`). |
| `TIMEZONE` | optional | Timezone for the nightly job and event times (default `Asia/Kolkata`). |
| `DISABLE_SCHEDULER` | optional | `1` turns off the in-server nightly job. |
| `DEMO_DRAFTS` | optional | `1` makes webhooks and the nightly job use template `[DEMO]` drafts instead of Claude. |

## Commands, in the order you would use them

Run them with `.venv/bin/python` (or activate the venv and use `python`).

```bash
python seed.py                          # build gym.db with fake data (wipes drafts too)
python check_data.py 7                  # spot-check one member's weekly visits
python score_all.py --top 10            # who is at risk, and why
python run_flow.py --limit 3            # Claude drafts for 3 members, then you review
python run_flow.py --demo-drafts        # same, with template drafts and no API key
python run_flow.py --review-only --branch hsr   # review pending drafts for one branch
python show_drafts.py --status sent     # what was recorded
python simulate_checkin.py 29           # DEMO ONLY: fake a check-in for member 29
python track.py                         # mark members who came back within 14 days
python stats.py --branch koramangala    # funnel, edit rate, win-back rate, by angle
python feedback.py                      # dry run: what would become new examples
python feedback.py --write              # append them to knowledge/past_winbacks.md
python -m pytest -q                     # all tests (no API calls, nothing sent)
```

## Demo

```bash
./demo.sh            # or: python demo.py
```

This runs the whole loop by itself on `demo_gym.db`, which is deleted and rebuilt
each run, so `gym.db` is never touched. A script stands in for the human reviewer: it
approves 2 drafts, edits 1, rejects 1 and leaves 1 pending. It then simulates two
members returning, and prints stats and the feedback dry run. Without an API key it
uses template `[DEMO]` drafts.

`python prototype.py` is the interactive version, where you do the reviewing yourself.

## Plugging in real systems

- **Gym data:** replace `MockGymDataSource` in `datasource.py` with a class that calls
  your gym software's API, using the same three methods. Nothing else changes.
- **Sending:** replace `MockSender` in `sender.py` (`get_sender()`) with a WhatsApp
  Business provider such as Twilio, Gupshup or the Meta Cloud API. `WhatsAppSender` is
  the stub to fill in.
- **Not built yet:** a web dashboard for branch managers, a real gym-software
  integration, and a real WhatsApp sender.

## Triggers

Triggers only ever create **pending** drafts. A person still approves every message
with `python run_flow.py --review-only`.

### Start the server

The app is built by a factory (`create_app`), so start it with `--factory`:

```bash
uvicorn server:create_app --factory --host 0.0.0.0 --port 8000
```

For development, add `--reload`. `python server.py` also works and listens on
127.0.0.1:8000. If `WEBHOOK_SECRET` is empty, the server prints why and refuses to start.

### Endpoints

Every webhook needs the header `X-Webhook-Secret: <WEBHOOK_SECRET>`, otherwise it returns 401.
`GET /health` needs no secret.

`POST /webhooks/gym-events` returns `202 {"accepted": true}` right away. A repeated
`event_id` returns `200 {"duplicate": true}` and is otherwise ignored.

```json
{"event_id": "gymsoft-81723", "type": "payment_failed", "member_id": 23,
 "timestamp": "2026-11-23T09:15:00+05:30", "payload": {}}
```

| type | What happens |
|---|---|
| `check_in` | Adds a check-in, then marks any sent draft whose member came back as won back |
| `payment_failed` | Sets the failed-payment date, then drafts if the member now scores at or above the threshold |
| `cancellation_request` | Drafts even below the threshold (never within 14 days of the last contact) |
| `freeze_request` | Same as cancellation |
| `membership_renewed` | Needs `payload.new_contract_end` (a future ISO date); updates the contract end and clears the failed payment |

`POST /webhooks/message-status` is for your WhatsApp provider's delivery callbacks. It
only works for sent drafts. A reply is recorded but does not count as a visit.

```json
{"draft_id": 12, "status": "read", "replied": true}
```

### Try it without curl

```bash
python send_test_event.py payment_failed 23
python send_test_event.py cancellation_request 1
python send_test_event.py membership_renewed 23 --url http://localhost:8000
python show_drafts.py --status pending
```

### Nightly job

At `NIGHTLY_HOUR` in `TIMEZONE`, the server runs `nightly.py`'s `run_nightly()`. It first
marks members who came back, then drafts for up to `NIGHTLY_LIMIT` at-risk members who
were not contacted in the last 14 days. Two runs can never overlap.

To use cron instead, start the server with `DISABLE_SCHEDULER=1`, so the job doesn't
run twice, and add the line below. Cron uses the machine's own timezone.

```
0 2 * * * cd "/path/to/GYM membership" && .venv/bin/python nightly.py >> nightly.log 2>&1
```

### Connecting a real gym system

The gym software must be able to reach the server over a public HTTPS URL. For local
testing, expose port 8000 with a tunnel such as ngrok (`ngrok http 8000`) or Cloudflare
Tunnel (`cloudflared tunnel --url http://localhost:8000`). Give the vendor the URL and
the secret.
