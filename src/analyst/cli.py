"""Command line: `bench` runs the Spider evaluation, `report` summarises it, `ask` queries one database, `serve` starts the web service."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .agent import CONFIGS, PROMPT_VERSION

RESULTS = Path("results")
LEDGER = RESULTS / "groq_ledger.json"
CACHE = RESULTS / "llm_cache"


def _models(arg: str | None) -> list[str] | None:
    return [m.strip() for m in arg.split(",") if m.strip()] if arg else None


def _llm(args, **extra):
    from .llm import LLM, LLMError
    try:
        return LLM(os.environ.get("GROQ_API_KEY"), models=_models(getattr(args, "models", None)), cache_dir=CACHE,
                   ledger_path=Path(os.environ.get("LEDGER_PATH", LEDGER)), **extra)
    except LLMError as exc:
        sys.exit(str(exc))


def cmd_bench(args) -> None:
    from .bench import run_bench
    from .spider import load_dev, sample

    qs = sample(load_dev(), args.n, args.seed)
    llm = _llm(args, mode=args.mode, max_requests=args.max_requests)
    res = run_bench(qs, args.config, llm, RESULTS / f"{args.config}_{PROMPT_VERSION}_s{args.seed}_n{args.n}.jsonl")
    print(json.dumps({**res, "network_requests": llm.usage.requests, "cache_hits": llm.usage.cache_hits}))
    llm.ledger.save()


def cmd_report(args) -> None:
    from .bench import load_results, markdown_table, summarize

    summaries = {}
    for path in sorted(RESULTS.glob("*_s*_n*.jsonl")):
        summaries[path.stem] = summarize([r for r in load_results(path).values() if not r.get("error")])
    (RESULTS / "summary.json").write_text(json.dumps(summaries, indent=2, sort_keys=True) + "\n")
    table = markdown_table(summaries)
    (RESULTS / "RESULTS.md").write_text("# Spider development-set results (execution accuracy)\n\n" + table)
    print(table)


def cmd_analyze(args) -> None:
    """Re-grade stored results with diagnostics (needs the Spider data); writes results/diagnostics.jsonl."""
    from collections import Counter

    from .bench import diagnose, load_results
    from .spider import db_path, load_dev
    from .sqlguard import open_readonly

    dev = {q["id"]: q for q in load_dev()}
    rows, conns = [], {}
    for path in sorted(RESULTS.glob("*_s*_n*.jsonl")):
        for r in load_results(path).values():
            q = dev[r["id"]]
            if q["db_id"] not in conns:
                conns[q["db_id"]] = open_readonly(db_path(q["db_id"]))
            rows.append({"id": r["id"], "config": r["config"], "db_id": r["db_id"], "model": r["model"], "question": q["question"],
                         "gold_sql": q["gold_sql"], "final_sql": r["final_sql"], "correct": r["correct"], "tool_calls": r["tool_calls"],
                         **diagnose(conns[q["db_id"]], r["final_sql"], q["gold_sql"])})
    (RESULTS / "diagnostics.jsonl").write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in rows))
    for config in sorted({x["config"] for x in rows}):
        print(config, dict(Counter(x["kind"] for x in rows if x["config"] == config)))


def cmd_ask(args) -> None:
    from .agent import run_analyst
    from .sqlguard import open_readonly

    conn = open_readonly(Path(args.db))
    run = run_analyst(_llm(args, mode="live"), args.question, conn, CONFIGS[args.config])
    print(run["text"] or run["clarify"])
    print(f"\n[{run['model']}, {run['tool_calls']} queries, {run['sql_errors']} errors]")


def cmd_serve(args) -> None:
    from .server import build_app, serve

    serve(build_app(_llm(args, mode="live"), per_ip=args.per_ip, daily_cap=args.daily_cap))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="analyst")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("bench", help="run the Spider execution-accuracy benchmark (resumable)")
    b.add_argument("--config", choices=list(CONFIGS), default="agent")
    b.add_argument("--n", type=int, default=200, help="questions in the sample (spread across databases)")
    b.add_argument("--seed", type=int, default=1)
    b.add_argument("--max-requests", type=int, default=900)
    b.add_argument("--models")
    b.add_argument("--mode", choices=["live", "replay"], default="live")
    b.set_defaults(fn=cmd_bench)
    r = sub.add_parser("report", help="summarise result files into results/RESULTS.md")
    r.set_defaults(fn=cmd_report)
    an = sub.add_parser("analyze", help="categorise wrong answers (needs the Spider data)")
    an.set_defaults(fn=cmd_analyze)
    a = sub.add_parser("ask", help="ask one question of a SQLite file")
    a.add_argument("db")
    a.add_argument("question")
    a.add_argument("--config", choices=list(CONFIGS), default="agent")
    a.add_argument("--models")
    a.set_defaults(fn=cmd_ask)
    s = sub.add_parser("serve", help="start the web service")
    s.add_argument("--models")
    s.add_argument("--per-ip", type=int, default=10)
    s.add_argument("--daily-cap", type=int, default=150)
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    args.fn(args)
    return 0
