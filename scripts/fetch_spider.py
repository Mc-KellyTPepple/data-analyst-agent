"""Download the Spider development set (questions, gold SQL, SQLite databases) into data/spider/.

Questions come from the Hugging Face datasets-server (xlangai/spider, validation split = the official
development set). The SQLite files come from a public mirror of the Spider databases. Run it on a machine
with internet access (the GitHub Actions workflow does).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import requests

OUT = Path("data/spider")
ROWS = "https://datasets-server.huggingface.co/rows"
MIRRORS = [
    "https://huggingface.co/datasets/prem-research/spider/resolve/main/database/{db}/{db}.sqlite",
    "https://huggingface.co/datasets/xlangai/spider/resolve/main/spider/database/{db}/{db}.sqlite",
]


def get(url: str, **kw) -> requests.Response:
    for attempt in range(4):
        r = requests.get(url, timeout=120, **kw)
        if r.status_code == 200:
            return r
        if r.status_code in (429, 500, 502, 503):
            time.sleep(5 * (attempt + 1))
            continue
        break
    return r


def fetch_questions() -> list[dict]:
    rows: list[dict] = []
    for offset in range(0, 1100, 100):
        r = get(ROWS, params={"dataset": "xlangai/spider", "config": "spider", "split": "validation", "offset": offset, "length": 100})
        r.raise_for_status()
        batch = r.json()["rows"]
        rows += [{"db_id": x["row"]["db_id"], "question": x["row"]["question"], "query": x["row"]["query"]} for x in batch]
        if len(batch) < 100:
            break
    return rows


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    questions = fetch_questions()
    if len(questions) != 1034:
        print(f"warning: expected 1034 dev questions, got {len(questions)}", file=sys.stderr)
    (OUT / "dev.json").write_text(json.dumps(questions, ensure_ascii=False), encoding="utf-8")
    missing = []
    for db in sorted({q["db_id"] for q in questions}):
        target = OUT / "database" / db / f"{db}.sqlite"
        if target.exists() and target.stat().st_size > 0:
            continue
        for mirror in MIRRORS:
            r = get(mirror.format(db=db))
            if r.status_code == 200 and r.content[:15] == b"SQLite format 3":
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(r.content)
                break
        else:
            missing.append(db)
    print(f"questions: {len(questions)}, databases: {len({q['db_id'] for q in questions}) - len(missing)} ok, missing: {missing}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
