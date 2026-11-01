import tempfile
import unittest
from pathlib import Path

from analyst.agent import CONFIGS, Config, extract_final_sql, run_analyst
from analyst.compare import is_ordered, same_result
from analyst.sqlguard import open_readonly
from tests.helpers import FakeLLM, make_db


class CompareTests(unittest.TestCase):
    def test_column_and_row_order_ignored_unless_ordered(self):
        self.assertTrue(same_result([["a", 1], ["b", 2]], [[2, "b"], [1, "a"]]))
        self.assertFalse(same_result([["a", 1], ["b", 2]], [[2, "b"], [1, "a"]], ordered=True))
        self.assertTrue(same_result([[1, "a"], [2, "b"]], [[1, "a"], [2, "b"]], ordered=True))

    def test_numbers_and_sizes(self):
        self.assertTrue(same_result([[1.00001]], [[1]]))
        self.assertFalse(same_result([[1]], [[1], [1]]))
        self.assertFalse(same_result([[1]], [[2]]))
        self.assertTrue(is_ordered("select a from t ORDER  BY a") and not is_ordered("select a from t"))


class ExtractTests(unittest.TestCase):
    def test_variants(self):
        self.assertEqual(extract_final_sql("Done.\nFINAL SQL:\nSELECT 1;"), "SELECT 1")
        self.assertEqual(extract_final_sql("FINAL SQL:\n```sql\nSELECT 2\n```"), "SELECT 2")
        self.assertEqual(extract_final_sql("try\n```sql\nSELECT 3\n```"), "SELECT 3")
        self.assertEqual(extract_final_sql("FINAL SQL:\nSELECT 'cut\n\nSorry, cut off.FINAL SQL:\nSELECT 4"), "SELECT 4")
        self.assertIsNone(extract_final_sql("I cannot do that"))
        self.assertIsNone(extract_final_sql(""))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_readonly(make_db(Path(self.tmp.name) / "d.sqlite"))

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_self_correction_after_sql_error(self):
        llm = FakeLLM([{"sql": "SELECT nme FROM singer"}, {"sql": "SELECT name FROM singer WHERE age > 30"},
                       "Two singers are over 30.\nFINAL SQL:\nSELECT name FROM singer WHERE age > 30"])
        run = run_analyst(llm, "Which singers are over 30?", self.conn, CONFIGS["agent"])
        self.assertEqual(run["final_sql"], "SELECT name FROM singer WHERE age > 30")
        self.assertEqual((run["tool_calls"], run["sql_errors"], run["calls"]), (2, 1, 3))
        self.assertIn("SQL error", llm.calls[1]["messages"][-1]["content"])    # the error was shown to the model
        self.assertIn("Cy", llm.calls[2]["messages"][-1]["content"])           # and so was the result

    def test_attempt_limit_blocks_extra_queries(self):
        llm = FakeLLM([{"sql": "SELECT 1"}, {"sql": "SELECT 2"}, "FINAL SQL:\nSELECT 1"])
        run = run_analyst(llm, "q", self.conn, CONFIGS["agent_1try"])
        self.assertEqual(run["tool_calls"], 2)
        self.assertIn("Query limit reached", llm.calls[2]["messages"][-1]["content"])
        self.assertEqual(run["last_ok_sql"], "SELECT 1")

    def test_oneshot_offers_no_tools_and_no_samples(self):
        llm = FakeLLM(["FINAL SQL:\nSELECT COUNT(*) FROM singer"])
        run = run_analyst(llm, "How many singers?", self.conn, CONFIGS["oneshot"])
        self.assertIsNone(llm.calls[0]["tools"])
        self.assertNotIn("sample rows", llm.calls[0]["messages"][0]["content"])
        self.assertNotIn("plain sentence", llm.calls[0]["messages"][0]["content"])   # benchmark prompt is unchanged
        self.assertEqual((run["final_sql"], run["calls"]), ("SELECT COUNT(*) FROM singer", 1))

    def test_falls_back_to_last_successful_query(self):
        llm = FakeLLM([{"sql": "SELECT COUNT(*) FROM singer"}, "There are 4 singers."])
        run = run_analyst(llm, "How many?", self.conn)
        self.assertEqual(run["final_sql"], "SELECT COUNT(*) FROM singer")

    def test_clarifying_question_when_enabled(self):
        cfg = Config("app", clarify=True)
        llm = FakeLLM(["CLARIFY: Do you mean the youngest or the oldest singer?"])
        run = run_analyst(llm, "Show the singer", self.conn, cfg)
        self.assertEqual(run["clarify"], "Do you mean the youngest or the oldest singer?")
        self.assertIsNone(run["final_sql"])
        self.assertIn("CLARIFY", llm.calls[0]["messages"][0]["content"])
        self.assertIn("one short plain sentence", llm.calls[0]["messages"][0]["content"])
        off = FakeLLM(["CLARIFY: which?"])
        self.assertIsNone(run_analyst(off, "x", self.conn, CONFIGS["agent"])["clarify"])

    def test_malformed_tool_call_is_resampled(self):
        from analyst.llm import LLMError
        llm = FakeLLM([LLMError("400 tool_use_failed: Parsing failed"), "FINAL SQL:\nSELECT 1"])
        self.assertEqual(run_analyst(llm, "q", self.conn)["final_sql"], "SELECT 1")

    def test_destructive_sql_from_the_model_never_runs(self):
        llm = FakeLLM([{"sql": "DROP TABLE singer"}, "FINAL SQL:\nSELECT COUNT(*) FROM singer"])
        run = run_analyst(llm, "q", self.conn)
        self.assertEqual(run["sql_errors"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM singer").fetchone()[0], 4)


if __name__ == "__main__":
    unittest.main()
