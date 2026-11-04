"""Small HTTP service (standard library only) around the analyst agent.

  GET  /health            liveness
  GET  /datasets          the built-in demo dataset
  POST /upload            {"name": "...", "csv": "..."} -> {"dataset": id, "table": ..., "columns": [...], "rows": n}
  POST /ask               {"question": "...", "dataset": "demo" | id} -> answer, sql, columns, rows, ...

It runs on a free host with a free Groq key, so it protects the quota: per-IP and daily limits, a question
length limit, a size limit on uploads, and the on-disk response cache.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import OrderedDict, defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .agent import APP_CONFIG, run_analyst
from .llm import LLMError
from .sqlguard import QueryError, open_readonly, run_select

ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")
DEMO_DB = Path(os.environ.get("DEMO_DB", "demo/demo.sqlite"))
MAX_CSV_BYTES, MAX_CSV_ROWS, MAX_COLUMNS = 400_000, 5_000, 30


class Limits:
    def __init__(self, per_ip_per_hour=10, daily_cap=150, clock=time.time):
        self.per_ip, self.daily_cap, self.clock = per_ip_per_hour, daily_cap, clock
        self.hits: dict[str, deque] = defaultdict(deque)
        self.day, self.count = "", 0
        self.lock = threading.Lock()

    def allow(self, ip: str, cost: int = 1) -> str | None:
        """None if allowed, otherwise the reason to refuse. Uploads use cost=0 for the daily cap but still count per IP."""
        with self.lock:
            now = self.clock()
            today = time.strftime("%Y-%m-%d", time.gmtime(now))
            if today != self.day:
                self.day, self.count = today, 0
            if cost and self.count >= self.daily_cap:
                return "The daily free quota for this demo is used up. Please try again tomorrow."
            q = self.hits[ip]
            while q and now - q[0] > 3600:
                q.popleft()
            if len(q) >= self.per_ip * (1 if cost else 3):
                return f"Rate limit: at most {self.per_ip} questions per hour per visitor."
            q.append(now)
            self.count += cost
            return None


def _ident(raw: str, used: set[str], i: int) -> str:
    name = re.sub(r"\W+", "_", (raw or "").strip()).strip("_").lower() or f"col{i + 1}"
    if name[0].isdigit():
        name = "c_" + name
    base, n = name, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _convert(col: list[str]):
    vals = [v.strip() for v in col if v is not None and v.strip() != ""]
    for caster, sql_type in ((int, "INTEGER"), (float, "REAL")):
        try:
            for v in vals:
                caster(v.replace(",", "") if caster is float else v)
            return sql_type, (lambda v, c=caster: None if v is None or v.strip() == "" else c(v.strip().replace(",", "") if c is float else v.strip()))
        except ValueError:
            continue
    return "TEXT", (lambda v: None if v is None or v.strip() == "" else v.strip())


def csv_to_db(name: str, text: str) -> tuple[sqlite3.Connection, str, list[str], int]:
    """Load one CSV into an in-memory SQLite table. Raises ValueError with a user-facing message."""
    if len(text.encode("utf-8")) > MAX_CSV_BYTES:
        raise ValueError(f"CSV too large (max {MAX_CSV_BYTES // 1000} KB).")
    rows = list(csv.reader(io.StringIO(text)))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("The CSV needs a header row and at least one data row.")
    header, data = rows[0], rows[1:]
    if len(data) > MAX_CSV_ROWS or len(header) > MAX_COLUMNS:
        raise ValueError(f"CSV too big (max {MAX_CSV_ROWS} rows and {MAX_COLUMNS} columns).")
    used: set[str] = set()
    cols = [_ident(h, used, i) for i, h in enumerate(header)]
    data = [(r + [""] * len(cols))[: len(cols)] for r in data]
    types = [_convert([r[i] for r in data]) for i in range(len(cols))]
    table = _ident(Path(name or "data").stem, set(), 0) or "data"
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(f'CREATE TABLE "{table}" (' + ", ".join(f'"{c}" {t[0]}' for c, t in zip(cols, types)) + ")")
    conn.executemany(f'INSERT INTO "{table}" VALUES ({",".join("?" * len(cols))})',
                     [[types[i][1](r[i]) for i in range(len(cols))] for r in data])
    conn.commit()
    conn.execute("PRAGMA query_only = ON")
    return conn, table, cols, len(data)


class App:
    def __init__(self, llm, demo_conn=None, limits: Limits | None = None, max_len: int = 500, runner=run_analyst, max_datasets: int = 20, ttl: int = 3600):
        self.llm, self.runner, self.max_len = llm, runner, max_len
        self.limits = limits or Limits()
        self.demo = demo_conn
        self.datasets: OrderedDict[str, tuple[float, sqlite3.Connection]] = OrderedDict()
        self.max_datasets, self.ttl = max_datasets, ttl
        self.lock = threading.Lock()   # one agent run at a time: connections and the Groq rate limiter are shared

    def _get(self, dataset: str):
        if dataset in ("", "demo"):
            return self.demo
        with self.lock:
            item = self.datasets.get(dataset)
            if item and time.time() - item[0] < self.ttl:
                return item[1]
            self.datasets.pop(dataset, None)
        return None

    def upload(self, name: str, text: str, ip: str) -> tuple[int, dict]:
        refused = self.limits.allow(ip, cost=0)
        if refused:
            return 429, {"error": refused}
        try:
            conn, table, cols, n = csv_to_db(name, text)
        except (ValueError, csv.Error, sqlite3.Error) as exc:
            return 400, {"error": str(exc)[:200]}
        token = secrets.token_urlsafe(9)
        with self.lock:
            self.datasets[token] = (time.time(), conn)
            while len(self.datasets) > self.max_datasets:
                self.datasets.popitem(last=False)
        return 200, {"dataset": token, "table": table, "columns": cols, "rows": n}

    def ask(self, question: str, dataset: str, ip: str) -> tuple[int, dict]:
        question = (question or "").strip()
        if not question:
            return 400, {"error": "Send a JSON body like {\"question\": \"...\"}."}
        if len(question) > self.max_len:
            return 400, {"error": f"Question too long (max {self.max_len} characters)."}
        conn = self._get(dataset)
        if conn is None:
            return 404, {"error": "That dataset is no longer available. Upload it again."}
        refused = self.limits.allow(ip)
        if refused:
            return 429, {"error": refused}
        t0 = time.monotonic()
        try:
            with self.lock:
                run = self.runner(self.llm, question, conn, APP_CONFIG)
                result = None
                if run["final_sql"]:
                    try:
                        result = run_select(conn, run["final_sql"], max_rows=50)
                    except QueryError as exc:
                        result = {"columns": [], "rows": [], "truncated": False, "error": str(exc)}
        except LLMError as exc:
            msg = ("The model provider is busy or out of free quota right now. Try again later."
                   if "quota" in str(exc).lower() or "budget" in str(exc).lower() else "The model provider returned an error. Try again.")
            return 503, {"error": msg}
        except Exception as exc:   # network failure, bad key, anything unexpected: never leak internals
            print(f"ask failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)   # type and message only, never the question
            return 502, {"error": "Could not reach the model provider. Try again later."}
        return 200, {"answer": run["text"], "clarify": run["clarify"], "sql": run["final_sql"], "result": result,
                     "queries": [{"sql": t["sql"], "ok": t["ok"]} for t in run["trace"]], "model": run["model"],
                     "seconds": round(time.monotonic() - t0, 1)}


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
            self.send_header("Vary", "Origin")

        def _json(self, code: int, obj: dict) -> None:
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors()
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):  # noqa: N802   CORS preflight
            self.send_response(204)
            self._cors()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._json(200, {"ok": True})
            elif self.path == "/datasets":
                self._json(200, {"datasets": [{"id": "demo", "name": "Demo shop (synthetic)"}] if app.demo is not None else []})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path not in ("/ask", "/upload"):
                return self._json(404, {"error": "not found"})
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if n > (MAX_CSV_BYTES + 4096 if self.path == "/upload" else 4096):
                    return self._json(413, {"error": "request too large"})
                data = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(data, dict):
                    raise ValueError
                strs = {k: data.get(k) for k in ("question", "dataset", "name", "csv") if data.get(k) is not None}
                if any(not isinstance(v, str) for v in strs.values()):
                    raise ValueError
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "Send a JSON body with string fields."})
            fwd = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()   # behind a proxy (Render) the client is the first entry
            ip = fwd or self.client_address[0]
            if self.path == "/upload":
                code, obj = app.upload(strs.get("name", "data"), strs.get("csv", ""), ip)
            else:
                code, obj = app.ask(strs.get("question", ""), strs.get("dataset", "demo"), ip)
            self._json(code, obj)

        def log_message(self, *a):   # keep logs free of question text
            pass

    return Handler


def build_app(llm, per_ip: int = 10, daily_cap: int = 150) -> App:
    demo = open_readonly(DEMO_DB, shared=True) if DEMO_DB.exists() else None
    return App(llm, demo, Limits(per_ip, daily_cap))


def serve(app: App, host: str = "0.0.0.0", port: int | None = None) -> None:
    port = port or int(os.environ.get("PORT", "7860"))
    print(f"listening on {host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), make_handler(app)).serve_forever()
