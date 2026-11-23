"""Feed finished outcomes back into the knowledge base as new past win-back examples.
Usage: python feedback.py          (dry run: only prints what would be added)
       python feedback.py --write  (appends to knowledge/past_winbacks.md and rebuilds the index)"""
import argparse
import re
from datetime import datetime, timedelta
from pathlib import Path

import rag
from db import connect

PAST_WINBACKS = rag.KNOWLEDGE_DIR / "past_winbacks.md"
WINDOW_DAYS = 14


def export_learnings(write: bool = False, path: Path | None = None, now: datetime | None = None) -> list[str]:
    """Return the sections added (or that would be added). [DEMO] placeholder drafts are
    shown in the dry run but never written, so fake messages never become examples."""
    path = path or PAST_WINBACKS
    now = now or datetime.now()
    existing = path.read_text() if path.exists() else ""
    cutoff = now - timedelta(days=WINDOW_DAYS)

    db = connect()
    try:
        sent = [dict(r) for r in db.execute("SELECT * FROM drafts WHERE status = 'sent' ORDER BY id")]
    finally:
        db.close()

    sections, writable = [], []
    for d in sent:
        finished = d.get("won_back") or (d.get("sent_at") and datetime.fromisoformat(d["sent_at"]) <= cutoff)
        if not finished or re.search(rf"draft #{d['id']}\b", existing):
            continue
        text = d.get("edited_message") or d["message"]
        returned = "yes" if d.get("won_back") else "no"
        section = f"## Message draft #{d['id']} | angle: {d['angle']} | returned: {returned}\n{text}\n"
        sections.append(section)
        if not text.startswith("[DEMO]"):
            writable.append(section)

    if write and writable:
        with path.open("a") as f:
            f.write("".join("\n" + s for s in writable))
        rag.build_index()
    return sections if not write else writable


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--write", action="store_true")
    write = p.parse_args().write
    sections = export_learnings(write=write)
    if write:
        print(f"Added {len(sections)} section(s) to {PAST_WINBACKS.name}\n")
    else:
        demo = sum(s.split("\n", 1)[1].startswith("[DEMO]") for s in sections)
        print(f"Dry run: {len(sections)} finished outcome(s) found; {len(sections) - demo} would be written "
              f"to {PAST_WINBACKS.name}" + (f" ({demo} [DEMO] placeholder(s) are always skipped)" if demo else "") + "\n")
    for s in sections:
        note = "   <- [DEMO] placeholder, never written" if s.split("\n", 1)[1].startswith("[DEMO]") else ""
        print(s.rstrip() + note + "\n")
    if not write and sections:
        print("Run with --write to append these.")
