"""Data pull: where member and check-in data comes from."""
import sqlite3
from datetime import datetime, timedelta
from typing import Protocol

from db import get_db_path



class GymDataSource(Protocol):
    def list_active_members(self) -> list[dict]: ...
    def get_member(self, member_id: int) -> dict | None: ...
    def get_checkins(self, member_id: int, since_days: int = 70) -> list[datetime]: ...


class MockGymDataSource:
    """Reads from the local gym.db. Opens and closes a connection per call."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path  # None means use GYM_DB (default gym.db)

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        db = sqlite3.connect(self.db_path or get_db_path())
        db.row_factory = sqlite3.Row
        try:
            return db.execute(sql, params).fetchall()
        finally:
            db.close()

    def list_active_members(self) -> list[dict]:
        rows = self._query("SELECT * FROM members WHERE status = 'active' ORDER BY id")
        return [dict(r) for r in rows]

    def get_member(self, member_id: int) -> dict | None:
        rows = self._query("SELECT * FROM members WHERE id = ?", (member_id,))
        return dict(rows[0]) if rows else None

    def get_checkins(self, member_id: int, since_days: int = 70) -> list[datetime]:
        since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d %H:%M")
        rows = self._query(
            "SELECT ts FROM checkins WHERE member_id = ? AND ts >= ? ORDER BY ts",
            (member_id, since))
        return [datetime.fromisoformat(r["ts"]) for r in rows]


def get_datasource() -> GymDataSource:
    # A real gym-software API class (same three methods) can replace
    # MockGymDataSource here later without changing any other file.
    return MockGymDataSource()
