"""Database helper: path from GYM_DB (default gym.db) and a safe drafts migration."""
import os
import sqlite3

# (column name, declaration). "trigger" is an SQL keyword, so it is always quoted.
NEW_DRAFT_COLUMNS = [("channel", ""), ("offer_id", ""), ("angle", ""), ("reasoning", ""), ("reviewer", ""),
                     ("decided_at", ""), ("won_back_at", ""), ("delivery_status", "TEXT"),
                     ("replied", "INTEGER DEFAULT 0"), ('"trigger"', "TEXT")]

EVENTS_TABLE = """CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, event_id TEXT UNIQUE, type, member_id,
    payload TEXT, event_ts, received_at
)"""


def get_db_path() -> str:
    return os.getenv("GYM_DB", "gym.db")


def migrate(db: sqlite3.Connection) -> None:
    """Create the events table and add any missing drafts columns. Never deletes data."""
    db.execute(EVENTS_TABLE)
    existing = {row[1] for row in db.execute("PRAGMA table_info(drafts)")}
    if existing:  # drafts table exists (seed.py creates it)
        for column, decl in NEW_DRAFT_COLUMNS:
            if column.strip('"') not in existing:
                db.execute(f"ALTER TABLE drafts ADD COLUMN {column} {decl}".rstrip())
    db.commit()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(get_db_path())
    db.row_factory = sqlite3.Row
    migrate(db)
    return db
