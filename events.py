"""Handling of gym events (check-ins, failed payments, requests, renewals).
Used by the webhook in server.py. Nothing here sends a message: events only update data
and, for some types, ask for a PENDING draft (the caller runs the pipeline)."""
import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

import track
from datasource import get_datasource
from db import connect

log = logging.getLogger("events")


class GymEvent(BaseModel):
    event_id: str | None = None
    type: Literal["check_in", "payment_failed", "freeze_request", "cancellation_request", "membership_renewed"]
    member_id: int
    timestamp: datetime | None = None
    payload: dict = Field(default_factory=dict)


class EventError(Exception):
    """A rejected event, with the HTTP status the caller should return."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code, self.detail = status_code, detail


@dataclass
class EventOutcome:
    duplicate: bool = False
    pipeline_kwargs: dict | None = None  # set when a draft should be created (run in the background)


def _local_naive(ts: datetime) -> datetime:
    """Store everything as local wall-clock time (TIMEZONE), like the rest of the database."""
    if ts.tzinfo is not None:
        ts = ts.astimezone(ZoneInfo(os.getenv("TIMEZONE", "Asia/Kolkata"))).replace(tzinfo=None)
    return ts


def process_event(event: GymEvent) -> EventOutcome:
    if get_datasource().get_member(event.member_id) is None:
        raise EventError(404, "Unknown member")
    ts = _local_naive(event.timestamp or datetime.now())

    new_end = None
    if event.type == "membership_renewed":  # validate before storing, so a bad event can be resent
        try:
            new_end = date.fromisoformat(str(event.payload.get("new_contract_end")))
        except ValueError:
            raise EventError(422, "payload.new_contract_end must be an ISO date (YYYY-MM-DD)")
        if new_end <= date.today():
            raise EventError(422, "payload.new_contract_end must be in the future")

    event_id = event.event_id or f"auto-{uuid.uuid4()}"
    db = connect()
    try:
        try:
            db.execute("INSERT INTO events (event_id, type, member_id, payload, event_ts, received_at)"
                       " VALUES (?,?,?,?,?,?)",
                       (event_id, event.type, event.member_id, json.dumps(event.payload),
                        ts.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")))
        except sqlite3.IntegrityError:  # same event_id delivered again: ignore it
            log.info("Duplicate event %s ignored", event_id)
            return EventOutcome(duplicate=True)
        if event.type == "check_in":
            db.execute("INSERT INTO checkins (member_id, ts) VALUES (?, ?)",
                       (event.member_id, ts.isoformat(sep=" ", timespec="seconds")))
        elif event.type == "payment_failed":
            db.execute("UPDATE members SET payment_failed_date = ? WHERE id = ?",
                       (ts.date().isoformat(), event.member_id))
        elif event.type == "membership_renewed":
            db.execute("UPDATE members SET contract_end = ?, payment_failed_date = NULL WHERE id = ?",
                       (new_end.isoformat(), event.member_id))
        db.commit()
    finally:
        db.close()
    log.info("Event %s: %s for member %s", event_id, event.type, event.member_id)

    if event.type == "check_in":
        track.update_outcomes()
    elif event.type == "payment_failed":
        return EventOutcome(pipeline_kwargs={"member_ids": [event.member_id], "trigger": "payment_failed"})
    elif event.type in ("cancellation_request", "freeze_request"):
        return EventOutcome(pipeline_kwargs={"member_ids": [event.member_id], "trigger": event.type,
                                             "force": True})
    return EventOutcome()
