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


class VoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_readonly(make_db(Path(self.tmp.name) / "d.sqlite"))
        self.cfg = CONFIGS["vote"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def fin(self, sql):
        return f"FINAL SQL:\n{sql}"

    def test_majority_result_wins_over_the_greedy_answer(self):
        young = "SELECT name FROM singer WHERE age < 30"
        old = "SELECT name FROM singer WHERE age > 30"
        llm = FakeLLM([self.fin(young), self.fin(old), self.fin("SELECT name FROM singer WHERE age >= 35 OR age = 41"), self.fin(old), self.fin("SELECT nope")])
        run = run_analyst(llm, "Which singers are over 30?", self.conn, self.cfg)
        self.assertEqual(run["final_sql"], old)                      # three candidates return the same rows (Cy, Di)
        self.assertEqual((run["calls"], run["sql_errors"], run["agreement"]), (5, 1, 0.6))
        self.assertEqual([c["temperature"] for c in llm.calls], [0.0, 0.5, 0.7, 0.9, 1.0])   # distinct requests, first is greedy
        self.assertTrue(all(c["tools"] is None for c in llm.calls))

    def test_row_and_column_order_do_not_split_votes(self):
        llm = FakeLLM([self.fin("SELECT name, age FROM singer ORDER BY age"), self.fin("SELECT age, name FROM singer ORDER BY age DESC"),
                       self.fin("SELECT name, age FROM singer"), self.fin("SELECT 1"), self.fin("SELECT 2")])
        run = run_analyst(llm, "q", self.conn, self.cfg)
        self.assertEqual(run["agreement"], 0.6)
        self.assertEqual(run["final_sql"], "SELECT name, age FROM singer ORDER BY age")

    def test_tie_goes_to_the_greedy_candidate(self):
        llm = FakeLLM([self.fin("SELECT 1"), self.fin("SELECT 2"), self.fin("SELECT 2"), self.fin("SELECT 1"), self.fin("SELECT 3")])
        self.assertEqual(run_analyst(llm, "q", self.conn, self.cfg)["final_sql"], "SELECT 1")

    def test_all_candidates_failing_returns_no_answer_gracefully(self):
        llm = FakeLLM([self.fin("SELECT nope")] * 5)
        run = run_analyst(llm, "q", self.conn, self.cfg)
        self.assertEqual((run["agreement"], run["sql_errors"]), (0.0, 5))
        self.assertIsNone(run["last_ok_sql"])

    def test_budget_exhaustion_propagates_but_one_bad_sample_does_not(self):
        from analyst.llm import BudgetExhausted, LLMError
        llm = FakeLLM([self.fin("SELECT 1"), LLMError("500 server error"), self.fin("SELECT 1"), self.fin("SELECT 1"), self.fin("SELECT 1")])
        run = run_analyst(llm, "q", self.conn, self.cfg)
        self.assertEqual((run["calls"], run["agreement"]), (4, 1.0))   # agreement is over the samples received
        with self.assertRaises(BudgetExhausted):
            run_analyst(FakeLLM([BudgetExhausted("quota")]), "q", self.conn, self.cfg)

    def test_vote_prompt_matches_the_one_shot_prompt(self):
        a, b = FakeLLM([self.fin("SELECT 1")] * 5), FakeLLM([self.fin("SELECT 1")])
        run_analyst(a, "same question", self.conn, self.cfg)
        run_analyst(b, "same question", self.conn, CONFIGS["oneshot"])
        self.assertEqual(a.calls[0]["messages"], b.calls[0]["messages"])   # so the greedy candidate reuses the cached one-shot response


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


class LeanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = open_readonly(make_db(Path(self.tmp.name) / "d.sqlite"))
        self.cfg = CONFIGS["lean"]

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_one_call_when_the_query_works(self):
        llm = FakeLLM(["FINAL SQL:\nSELECT COUNT(*) FROM singer"])
        run = run_analyst(llm, "How many singers?", self.conn, self.cfg)
        self.assertEqual((run["calls"], run["final_sql"], run["sql_errors"]), (1, "SELECT COUNT(*) FROM singer", 0))
        self.assertIsNone(llm.calls[0]["tools"])
        self.assertIn("sample rows", llm.calls[0]["messages"][0]["content"])

    def test_second_call_only_on_error_and_shows_the_error(self):
        llm = FakeLLM(["FINAL SQL:\nSELECT nme FROM singer", "FINAL SQL:\nSELECT name FROM singer"])
        run = run_analyst(llm, "Names?", self.conn, self.cfg)
        self.assertEqual((run["calls"], run["final_sql"], run["sql_errors"]), (2, "SELECT name FROM singer", 1))
        self.assertIn("no such column", llm.calls[1]["messages"][-1]["content"])

    def test_empty_result_triggers_one_retry_and_a_confirmed_empty_answer_is_kept(self):
        llm = FakeLLM(["FINAL SQL:\nSELECT name FROM singer WHERE age > 99", "FINAL SQL:\nSELECT name FROM singer WHERE age > 99"])
        run = run_analyst(llm, "Singers over 99?", self.conn, self.cfg)
        self.assertEqual((run["calls"], run["final_sql"]), (2, "SELECT name FROM singer WHERE age > 99"))
        self.assertIn("no rows", llm.calls[1]["messages"][-1]["content"])

    def test_never_more_than_one_retry_and_a_working_earlier_query_beats_a_broken_retry(self):
        llm = FakeLLM(["FINAL SQL:\nSELECT name FROM singer WHERE age > 99", "FINAL SQL:\nSELECT nope"])
        run = run_analyst(llm, "q", self.conn, self.cfg)
        self.assertEqual((run["calls"], run["final_sql"]), (2, "SELECT name FROM singer WHERE age > 99"))

    def test_web_app_config_asks_for_clarification_and_gives_no_prose_answer(self):
        from analyst.agent import APP_CONFIG
        asks = run_analyst(FakeLLM(["CLARIFY: Which singer?"]), "Show the singer", self.conn, APP_CONFIG)
        self.assertEqual((asks["clarify"], asks["final_sql"], asks["calls"]), ("Which singer?", None, 1))
        llm = FakeLLM(["Sure, there are 4.\nFINAL SQL:\nSELECT COUNT(*) FROM singer"])
        run = run_analyst(llm, "How many?", self.conn, APP_CONFIG)
        self.assertEqual(run["text"], "FINAL SQL:\nSELECT COUNT(*) FROM singer")   # prose written before seeing results is never shown
        self.assertNotIn("plain sentence", llm.calls[0]["messages"][0]["content"])
