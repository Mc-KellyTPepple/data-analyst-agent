"""Describe a SQLite database to the model: CREATE statements, plus a few sample rows per table."""
from __future__ import annotations

import sqlite3


def _cell(v) -> str:
    s = "NULL" if v is None else str(v)
    return s if len(s) <= 40 else s[:37] + "..."


def describe(conn: sqlite3.Connection, sample_rows: int = 3, max_chars: int = 7000) -> str:
    tables = conn.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    parts = []
    for name, sql in tables:
        block = " ".join((sql or "").split())
        if sample_rows > 0:
            try:
                cur = conn.execute(f'SELECT * FROM "{name}" LIMIT {int(sample_rows)}')
                cols = [d[0] for d in cur.description]
                rows = cur.fetchall()
                if rows:
                    lines = [" | ".join(cols)] + [" | ".join(_cell(v) for v in r) for r in rows]
                    block += "\n-- sample rows:\n-- " + "\n-- ".join(lines)
            except sqlite3.Error:
                pass
        parts.append(block)
    text = "\n\n".join(parts)
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n-- (schema truncated)"
    return text
