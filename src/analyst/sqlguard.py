"""Model-written SQL is untrusted. This module runs it safely.

Defences, in layers: the database is opened read-only, a SQLite authorizer allows only
SELECT-type operations (no PRAGMA, ATTACH, writes or extension loading), only one statement
runs, rows are capped, and a time limit aborts runaway queries.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class QueryError(ValueError):
    """The query was rejected or failed."""


def open_readonly(path: Path, shared: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True, check_same_thread=not shared)
    conn.text_factory = lambda b: b.decode("utf-8", errors="replace")   # some public databases contain invalid UTF-8
    return conn


_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
_BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _authorizer(action, arg1, arg2, dbname, source):
    if action not in _ALLOWED:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_select(conn: sqlite3.Connection, sql: str, max_rows: int = 200, timeout_s: float = 5.0) -> dict:
    """Run one SELECT. Returns {'columns': [...], 'rows': [[...]], 'truncated': bool}; raises QueryError."""
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("empty query")
    if ";" in sql.replace("';'", ""):   # cheap early rejection; the real guarantee is sqlite's one-statement rule
        raise QueryError("only one statement is allowed")
    deadline = time.monotonic() + timeout_s
    conn.set_authorizer(_authorizer)
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        return {"columns": columns, "rows": [list(r) for r in rows[:max_rows]], "truncated": len(rows) > max_rows}
    except sqlite3.Error as exc:
        msg = str(exc)
        if "not authorized" in msg or "interrupted" in msg:
            msg = "query not allowed or took too long" if "not authorized" in msg else "query took too long (over 5 seconds)"
        raise QueryError(msg) from exc
    finally:
        conn.set_authorizer(None)
        conn.set_progress_handler(None, 0)
