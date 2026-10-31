"""Execution-accuracy comparison, in the spirit of Spider's official evaluation.

Two queries match when they return the same rows, ignoring column names and column order. Row
order matters only when the gold query has an ORDER BY. Floats are compared to 4 decimals.
"""
from __future__ import annotations

import re
from collections import Counter


def _norm(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return round(float(v), 4)
    return v


def _row_key(row) -> tuple:
    return tuple(sorted((_norm(v) for v in row), key=repr))


def is_ordered(gold_sql: str) -> bool:
    return bool(re.search(r"\border\s+by\b", gold_sql or "", re.I))


def same_result(pred_rows: list, gold_rows: list, ordered: bool = False) -> bool:
    if len(pred_rows) != len(gold_rows):
        return False
    p = [_row_key(r) for r in pred_rows]
    g = [_row_key(r) for r in gold_rows]
    return p == g if ordered else Counter(p) == Counter(g)
