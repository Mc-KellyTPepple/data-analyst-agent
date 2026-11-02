"""Resumable Spider execution-accuracy benchmark.

For each question: run the agent against the database, execute its final SQL and the gold SQL
(both read-only), and compare the results. Results are appended to a JSONL file, one line per
question, so a run that hits the free daily quota can simply be continued the next day.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path

from .agent import CONFIGS, run_analyst
from .compare import is_ordered, same_result
from .llm import BudgetExhausted, CacheMiss
from .schema import describe
from .spider import ROOT, db_path
from .sqlguard import QueryError, open_readonly, run_select


def load_results(path: Path) -> dict[str, dict]:
    path = Path(path)
    if not path.exists():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in rows}


def grade(conn, final_sql: str | None, gold_sql: str) -> tuple[bool, str]:
    if not final_sql:
        return False, "no_sql"
    try:
        pred = run_select(conn, final_sql, max_rows=100_000, timeout_s=20)
    except QueryError as exc:
        return False, f"pred_error: {str(exc)[:80]}"
    gold = run_select(conn, gold_sql, max_rows=100_000, timeout_s=20)
    return (True, "match") if same_result(pred["rows"], gold["rows"], is_ordered(gold_sql)) else (False, "mismatch")


def diagnose(conn, final_sql: str | None, gold_sql: str) -> dict:
    """Why a prediction differs from the gold query: wrong shape (columns/rows) or same shape with different values."""
    gold = run_select(conn, gold_sql, max_rows=100_000, timeout_s=20)
    out = {"gold_cols": len(gold["columns"]), "gold_rows": len(gold["rows"])}
    if not final_sql:
        return {**out, "kind": "no_sql"}
    try:
        pred = run_select(conn, final_sql, max_rows=100_000, timeout_s=20)
    except QueryError:
        return {**out, "kind": "error"}
    out.update(pred_cols=len(pred["columns"]), pred_rows=len(pred["rows"]))
    if same_result(pred["rows"], gold["rows"], is_ordered(gold_sql)):
        kind = "match"
    elif out["pred_cols"] > out["gold_cols"]:
        kind = "extra_columns"
    elif out["pred_cols"] < out["gold_cols"]:
        kind = "missing_columns"
    elif out["pred_rows"] > out["gold_rows"]:
        kind = "more_rows"
    elif out["pred_rows"] < out["gold_rows"]:
        kind = "fewer_rows"
    else:
        kind = "same_shape_other_values"
    return {**out, "kind": kind}


def run_bench(questions: list[dict], config: str, llm, out_path: Path, root: Path = ROOT, limit: int | None = None, progress=print) -> dict:
    cfg = CONFIGS[config]
    out_path = Path(out_path)
    done = {k: v for k, v in load_results(out_path).items() if not v.get("error")}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():   # failed attempts are retried, so drop them from the file
        out_path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in done.values()), encoding="utf-8")
    todo = [q for q in questions if q["id"] not in done]
    if limit is not None:
        todo = todo[:limit]
    stopped = None
    conns: dict[str, tuple] = {}
    with open(out_path, "a", encoding="utf-8") as fh:
        for i, q in enumerate(todo, 1):
            t0 = time.monotonic()
            try:
                if q["db_id"] not in conns:
                    conn = open_readonly(db_path(q["db_id"], root))
                    conns[q["db_id"]] = (conn, describe(conn, sample_rows=3 if cfg.samples else 0))
                conn, schema_text = conns[q["db_id"]]
                run = run_analyst(llm, q["question"], conn, cfg, schema_text=schema_text)
                ok, reason = grade(conn, run["final_sql"], q["gold_sql"])
                rec = {"id": q["id"], "db_id": q["db_id"], "config": config, "prompt_version": run["prompt_version"], "correct": ok, "reason": reason,
                       "final_sql": run["final_sql"], "agreement": run.get("agreement"), "model": run["model"], "calls": run["calls"], "tool_calls": run["tool_calls"],
                       "gold_sql": q["gold_sql"], "question": q["question"], "diag": diagnose(conn, run["final_sql"], q["gold_sql"]),
                       "sql_errors": run["sql_errors"], "prompt_tokens": run["prompt_tokens"], "completion_tokens": run["completion_tokens"],
                       "seconds": round(time.monotonic() - t0, 2), "error": None}
            except BudgetExhausted as exc:
                stopped = str(exc)
                break
            except CacheMiss as exc:
                stopped = f"cache miss: {exc}"
                break
            except Exception as exc:   # one bad question should not end the run
                rec = {"id": q["id"], "db_id": q["db_id"], "config": config, "correct": False, "reason": "error", "final_sql": None, "model": "",
                       "calls": 0, "tool_calls": 0, "sql_errors": 0, "prompt_tokens": 0, "completion_tokens": 0,
                       "seconds": round(time.monotonic() - t0, 2), "error": f"{type(exc).__name__}: {exc}"[:300]}
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
            fh.flush()
            progress(f"[{i}/{len(todo)}] {q['id']} {'ok' if rec['correct'] else rec['reason']}")
    for conn, _ in conns.values():
        conn.close()
    finished = {k for k, v in load_results(out_path).items() if not v.get("error")}
    return {"attempted": len(todo), "stopped": stopped, "remaining": len([q for q in questions if q["id"] not in finished])}


def summarize(results: list[dict]) -> dict:
    n = len(results)
    if not n:
        return {"questions": 0}
    ok = sum(r["correct"] for r in results)
    by_db: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_db[r["db_id"]].append(r["correct"])
    reasons: dict[str, int] = defaultdict(int)
    for r in results:
        reasons["match" if r["correct"] else r["reason"].split(":")[0]] += 1
    models: dict[str, int] = defaultdict(int)
    for r in results:
        models[r["model"] or "none"] += 1
    by_agreement: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        if r.get("agreement") is not None:
            by_agreement["unanimous" if r["agreement"] >= 1.0 else "split"].append(r["correct"])
    return {
        "questions": n,
        "accuracy_by_agreement": {k: {"n": len(v), "accuracy": round(100 * sum(v) / len(v), 1)} for k, v in sorted(by_agreement.items())},
        "execution_accuracy": round(100 * ok / n, 1),
        "outcomes": dict(sorted(reasons.items())),
        "mean_llm_calls": round(sum(r["calls"] for r in results) / n, 2),
        "mean_sql_errors": round(sum(r["sql_errors"] for r in results) / n, 2),
        "mean_tokens": round(sum(r["prompt_tokens"] + r["completion_tokens"] for r in results) / n),
        "mean_seconds": round(sum(r["seconds"] for r in results) / n, 1),
        "models": dict(sorted(models.items())),
        "databases_worst": sorted(((db, round(100 * sum(v) / len(v), 1), len(v)) for db, v in by_db.items()), key=lambda x: (x[1], -x[2]))[:5],
    }


def markdown_table(summaries: dict[str, dict]) -> str:
    head = "| Configuration | Questions | Execution accuracy | LLM calls / q | SQL errors / q | Tokens / q |\n|---|---|---|---|---|---|\n"
    rows = [f"| {name} | {s['questions']} | {s['execution_accuracy']}% | {s['mean_llm_calls']} | {s['mean_sql_errors']} | {s['mean_tokens']} |"
            for name, s in summaries.items() if s.get("questions")]
    return head + "\n".join(rows) + "\n"
