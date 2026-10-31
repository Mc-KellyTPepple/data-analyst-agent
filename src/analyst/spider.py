"""Spider (Yu et al., 2018) development set: questions, gold SQL and SQLite databases.

The data is not stored in this repository (it is licensed CC BY-SA 4.0); `scripts/fetch_spider.py`
downloads it. Layout: data/spider/dev.json and data/spider/database/<db_id>/<db_id>.sqlite
"""
from __future__ import annotations

import json
import random
from pathlib import Path

ROOT = Path("data/spider")


def load_dev(root: Path = ROOT) -> list[dict]:
    rows = json.loads((root / "dev.json").read_text(encoding="utf-8"))
    out = []
    for i, r in enumerate(rows):
        out.append({"id": f"dev-{i:04d}", "db_id": r["db_id"], "question": r["question"], "gold_sql": r["query"]})
    return out


def sample(questions: list[dict], n: int, seed: int = 1) -> list[dict]:
    """A reproducible sample spread across databases (round-robin over shuffled per-database lists)."""
    rng = random.Random(seed)
    by_db: dict[str, list[dict]] = {}
    for q in questions:
        by_db.setdefault(q["db_id"], []).append(q)
    for lst in by_db.values():
        rng.shuffle(lst)
    dbs = sorted(by_db)
    picked: list[dict] = []
    while len(picked) < min(n, len(questions)):
        for db in dbs:
            if by_db[db] and len(picked) < n:
                picked.append(by_db[db].pop())
    return sorted(picked, key=lambda q: q["id"])


def db_path(db_id: str, root: Path = ROOT) -> Path:
    return root / "database" / db_id / f"{db_id}.sqlite"
