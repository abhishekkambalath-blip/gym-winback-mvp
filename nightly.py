"""Nightly job: track outcomes, then draft for at-risk members (capped to limit API cost).
Creates PENDING drafts only. Runs inside the server via APScheduler, or from cron:
    python nightly.py"""
import logging
import os
from dataclasses import asdict

from dotenv import load_dotenv

import track
from pipeline import run_pipeline

load_dotenv()
log = logging.getLogger("nightly")


def run_nightly(limit: int | None = None, placeholders: bool | None = None) -> dict:
    limit = limit if limit is not None else int(os.getenv("NIGHTLY_LIMIT") or 20)
    won_back = track.update_outcomes()
    r = run_pipeline(trigger="nightly", limit=limit, placeholders=placeholders)
    log.info("Nightly run: %d newly won back; scored %d, flagged %d, drafted %d (limit %d), "
             "skipped %d recently contacted, failed %d",
             won_back, r.scored, r.flagged, r.drafted, limit, r.skipped_recent, r.failed)
    return {"won_back": won_back, **asdict(r)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_nightly()
