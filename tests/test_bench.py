import json
import tempfile
import unittest
from pathlib import Path

from analyst.bench import diagnose, load_results, markdown_table, run_bench, summarize
from analyst.sqlguard import open_readonly
from analyst.llm import BudgetExhausted
from analyst.spider import db_path, sample
from tests.helpers import FakeLLM, make_db


def questions():
    return [{"id": "dev-0000", "db_id": "music", "question": "How many singers?", "gold_sql": "SELECT COUNT(*) FROM singer"},
            {"id": "dev-0001", "db_id": "music", "question": "Names of French singers", "gold_sql": "SELECT name FROM singer WHERE country='France'"},
            {"id": "dev-0002", "db_id": "music", "question": "Oldest singer", "gold_sql": "SELECT name FROM singer ORDER BY age DESC LIMIT 1"}]


class BenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_db(db_path("music", self.root))
        self.out = self.root / "out.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_grading_resume_and_summary(self):
        llm = FakeLLM(["FINAL SQL:\nSELECT COUNT(*) FROM singer",                 # correct
                       "FINAL SQL:\nSELECT name FROM singer",                    # wrong rows
                       "FINAL SQL:\nSELECT nme FROM singer"])                    # does not run
        res = run_bench(questions(), "oneshot", llm, self.out, root=self.root, progress=lambda *_: None)
        self.assertEqual((res["attempted"], res["remaining"], res["stopped"]), (3, 0, None))
        rows = load_results(self.out)
        self.assertEqual([rows[k]["correct"] for k in sorted(rows)], [True, False, False])
        self.assertEqual([rows[k]["reason"].split(":")[0] for k in sorted(rows)], ["match", "mismatch", "pred_error"])
        again = run_bench(questions(), "oneshot", FakeLLM([]), self.out, root=self.root, progress=lambda *_: None)
        self.assertEqual(again["attempted"], 0)
        s = summarize(list(rows.values()))
        self.assertEqual((s["questions"], s["execution_accuracy"]), (3, 33.3))
        self.assertIn("| oneshot | 3 | 33.3% |", markdown_table({"oneshot": s}))

    def test_budget_exhaustion_stops_cleanly_and_resumes(self):
        class Out(FakeLLM):
            def chat(self, *a, **k):
                if not self.script:
                    raise BudgetExhausted("quota")
                return super().chat(*a, **k)
        llm = Out(["FINAL SQL:\nSELECT COUNT(*) FROM singer"])
        res = run_bench(questions(), "oneshot", llm, self.out, root=self.root, progress=lambda *_: None)
        self.assertEqual((res["attempted"], res["remaining"]), (3, 2))
        self.assertTrue(res["stopped"])
        self.assertEqual(len(load_results(self.out)), 1)

    def test_errors_are_retried_not_kept(self):
        self.out.write_text(json.dumps({"id": "dev-0000", "db_id": "music", "error": "boom", "correct": False, "reason": "error"}) + "\n")
        llm = FakeLLM(["FINAL SQL:\nSELECT COUNT(*) FROM singer"])
        run_bench(questions()[:1], "oneshot", llm, self.out, root=self.root, progress=lambda *_: None)
        self.assertTrue(load_results(self.out)["dev-0000"]["correct"])


class DiagnoseTests(unittest.TestCase):
    def test_kinds(self):
        with tempfile.TemporaryDirectory() as d:
            conn = open_readonly(make_db(Path(d) / "m.sqlite"))
            gold = "SELECT name FROM singer WHERE country='France'"
            k = lambda sql: diagnose(conn, sql, gold)["kind"]  # noqa: E731
            self.assertEqual(k(gold), "match")
            self.assertEqual(k("SELECT name, age FROM singer WHERE country='France'"), "extra_columns")
            self.assertEqual(k("SELECT name FROM singer"), "more_rows")
            self.assertEqual(k("SELECT name FROM singer WHERE country='France' AND age>30"), "fewer_rows")
            self.assertEqual(k("SELECT country FROM singer WHERE country='France'"), "same_shape_other_values")
            self.assertEqual(k("SELECT nope"), "error")
            self.assertEqual(k(None), "no_sql")
            conn.close()


class SampleTests(unittest.TestCase):
    def test_reproducible_and_spread_across_databases(self):
        qs = [{"id": f"dev-{i:04d}", "db_id": f"db{i % 4}", "question": "", "gold_sql": ""} for i in range(40)]
        a, b = sample(qs, 8, seed=3), sample(qs, 8, seed=3)
        self.assertEqual(a, b)
        self.assertEqual({q["db_id"] for q in a}, {"db0", "db1", "db2", "db3"})
        self.assertNotEqual(a, sample(qs, 8, seed=4))
        self.assertEqual(len(sample(qs, 100)), 40)


if __name__ == "__main__":
    unittest.main()
