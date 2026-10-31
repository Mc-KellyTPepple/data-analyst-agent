"""The data analyst agent: question in, checked SQL and answer out.

Configurations (used for the ablation study):
  oneshot         the model writes SQL once from the schema, no tools (baseline)
  agent_1try      tools, but only one query may be run (no self-correction)
  agent_nosamples tools and self-correction, schema without sample rows
  agent           tools, self-correction, schema with sample rows (the default)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .llm import LLMError
from .schema import describe
from .sqlguard import QueryError, run_select

PROMPT_VERSION = "v2"   # v1: first benchmark run; v2: exact-columns rule, last FINAL SQL marker (both from the v1 failure analysis)


@dataclass(frozen=True)
class Config:
    name: str
    tools: bool = True
    attempts: int = 5          # how many run_sql calls the model may make
    samples: bool = True       # include sample rows in the schema
    clarify: bool = False      # allow a clarifying question instead of an answer (off in the benchmark)


CONFIGS = {
    "oneshot": Config("oneshot", tools=False, attempts=0, samples=False),
    "agent_1try": Config("agent_1try", attempts=1),
    "agent_nosamples": Config("agent_nosamples", samples=False),
    "agent": Config("agent"),
}

_SYSTEM = """You are a careful data analyst answering a question about a SQLite database.

{schema}

Rules:
- Write SQLite SQL using only the tables and columns above, with their exact names.
- Select ONLY the columns the question asks for, in the order it mentions them. Do not add counts, ids, sort keys or other helpful extras, and do not alias columns.
- Do not add LIMIT unless the question asks for a limited number. Do not CAST columns unless a query error or the sample values show it is needed.
{tool_rules}- Only SELECT statements work. Never try to change data.
{clarify_rule}- End your reply with the final query, exactly like this:
FINAL SQL:
<the query>"""

_TOOL_RULES = ("- Use run_sql to try queries. Read each result. If a query fails, or the result looks wrong (empty when it should not be, "
               "wrong columns, duplicates), fix it and run it again. Explore values with run_sql first if you are unsure how data is stored.\n")
_CLARIFY_RULE = ("- If the question is ambiguous in a way that would change the result, reply with one line starting 'CLARIFY:' and a short "
                 "question instead of querying.\n")

TOOL_SQL = {"type": "function", "function": {
    "name": "run_sql",
    "description": "Run one read-only SQLite SELECT and see up to 20 rows of the result.",
    "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]}}}

_RECOVERABLE = ("tool_use_failed", "Failed to call a function", "Parsing failed", "failed_generation")


def build_system(schema_text: str, cfg: Config) -> str:
    return _SYSTEM.format(schema=schema_text, tool_rules=_TOOL_RULES if cfg.tools else "",
                          clarify_rule=_CLARIFY_RULE if cfg.clarify else "")


def extract_final_sql(text: str) -> str | None:
    """The query after 'FINAL SQL:'; falls back to the last fenced block."""
    if not text:
        return None
    parts = re.split(r"FINAL SQL:", text, flags=re.I)   # the model may restate it after a cut-off draft; the last one wins
    body = parts[-1] if len(parts) > 1 else None
    if body is None:
        blocks = re.findall(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
        body = blocks[-1] if blocks else None
    if body is None:
        return None
    fenced = re.search(r"```(?:sql)?\s*(.*?)```", body, re.S | re.I)
    body = fenced.group(1) if fenced else body.replace("```", "")
    body = body.strip().rstrip(";").strip()
    return body or None


def _format_result(res: dict, limit: int = 20) -> str:
    lines = [" | ".join(res["columns"])]
    for row in res["rows"][:limit]:
        lines.append(" | ".join("NULL" if v is None else str(v)[:60] for v in row))
    if not res["rows"]:
        lines.append("(no rows)")
    extra = len(res["rows"]) - limit
    if extra > 0 or res["truncated"]:
        lines.append(f"... more rows not shown ({'at least ' if res['truncated'] else ''}{max(extra, 0)} more)")
    return "\n".join(lines)


def _chat(llm, messages, tools, max_tokens):
    try:
        return llm.chat(messages, tools=tools, max_tokens=max_tokens)
    except LLMError as exc:
        if not any(t in str(exc) for t in _RECOVERABLE):
            raise
    try:   # malformed tool call from the model: resample with a little noise, then without tools
        return llm.chat(messages, tools=tools, max_tokens=max_tokens, temperature=0.4)
    except LLMError as exc:
        if not any(t in str(exc) for t in _RECOVERABLE):
            raise
    return llm.chat(messages, tools=None, max_tokens=max_tokens, temperature=0.4)


def run_analyst(llm, question: str, conn, cfg: Config = CONFIGS["agent"], schema_text: str | None = None, max_steps: int = 7) -> dict:
    if schema_text is None:
        schema_text = describe(conn, sample_rows=3 if cfg.samples else 0)
    messages = [{"role": "system", "content": build_system(schema_text, cfg)}, {"role": "user", "content": question}]
    prompt = completion = calls = tool_calls = errors = 0
    model, text = "", ""
    trace: list[dict] = []
    last_ok_sql, last_ok_result = None, None
    for step in range(max_steps):
        offer_tools = cfg.tools and step < max_steps - 1
        resp = _chat(llm, messages, [TOOL_SQL] if offer_tools else None, 1200)
        calls += 1
        model = resp["model"]
        prompt += resp["usage"]["prompt_tokens"]
        completion += resp["usage"]["completion_tokens"]
        msg = resp["message"]
        messages.append(msg)
        if not msg.get("tool_calls"):
            text = msg.get("content") or ""
            break
        for tc in msg["tool_calls"]:
            tool_calls += 1
            try:
                sql = json.loads(tc["function"].get("arguments") or "{}").get("sql", "")
            except (ValueError, AttributeError):
                sql = ""
            if tool_calls > cfg.attempts:
                out = "Query limit reached. Give your final answer now, ending with FINAL SQL:"
                ok = False
            else:
                try:
                    res = run_select(conn, sql, max_rows=200)
                    out, ok = _format_result(res), True
                    last_ok_sql, last_ok_result = sql.strip().rstrip(";"), res
                except QueryError as exc:
                    out, ok = f"SQL error: {exc}", False
                    errors += 1
            trace.append({"sql": sql, "ok": ok, "out": out[:300]})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
    final_sql = extract_final_sql(text) or last_ok_sql
    clarify = None
    m = re.search(r"^\s*CLARIFY:\s*(.+)$", text, re.M)
    if cfg.clarify and m and not extract_final_sql(text):
        clarify, final_sql = m.group(1).strip(), None
    return {"text": text, "final_sql": final_sql, "clarify": clarify, "tool_calls": tool_calls, "calls": calls, "sql_errors": errors,
            "model": model, "prompt_tokens": prompt, "completion_tokens": completion, "trace": trace,
            "last_ok_sql": last_ok_sql, "last_ok_result": last_ok_result, "prompt_version": PROMPT_VERSION}
