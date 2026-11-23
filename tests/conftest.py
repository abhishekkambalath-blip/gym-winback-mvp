import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import seed  # noqa: E402  (only for its SCHEMA)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """A temporary database with 3 members, pointed to by GYM_DB."""
    path = tmp_path / "test.db"
    monkeypatch.setenv("GYM_DB", str(path))
    db = sqlite3.connect(path)
    db.executescript(seed.SCHEMA)
    db.executemany(
        "INSERT INTO members (id, name, branch, plan, phone, join_date, contract_end, fav_class, usual_time)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        [(1, "Priya Sharma", "HSR Layout", "monthly", "+91 9811111111", "2025-01-01", "2027-01-01", "Yoga", "evening"),
         (2, "Rahul Nair", "Koramangala", "annual", "+91 9822222222", "2025-01-01", "2027-01-01", "Spin", "morning"),
         (3, "Meera Rao", "Indiranagar", "monthly", "+91 9833333333", "2025-01-01", "2027-01-01", "HIIT", "lunch")])
    db.commit()
    db.close()
    return path
