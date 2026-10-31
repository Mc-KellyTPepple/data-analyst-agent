import sqlite3
import tempfile
import unittest
from pathlib import Path

from analyst.sqlguard import QueryError, open_readonly, run_select
from analyst.schema import describe
from tests.helpers import make_db


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = make_db(Path(self.tmp.name) / "d.sqlite")
        self.conn = open_readonly(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_select_and_cte_work(self):
        r = run_select(self.conn, "SELECT name FROM singer WHERE age > 30 ORDER BY name;")
        self.assertEqual(r["rows"], [["Cy"], ["Di"]])
        r = run_select(self.conn, "WITH t AS (SELECT country, COUNT(*) c FROM singer GROUP BY country) SELECT * FROM t ORDER BY country")
        self.assertEqual(r["rows"], [["France", 2], ["Nigeria", 2]])

    def test_writes_and_ddl_are_blocked(self):
        for sql in ["DROP TABLE singer", "DELETE FROM singer", "UPDATE singer SET age=1", "INSERT INTO singer VALUES (9,'x','y',1)",
                    "CREATE TABLE t(x)", "PRAGMA table_info(singer)", "ATTACH DATABASE ':memory:' AS m", "SELECT load_extension('x')"]:
            with self.assertRaises(QueryError, msg=sql):
                run_select(self.conn, sql)
        self.assertEqual(run_select(self.conn, "SELECT COUNT(*) FROM singer")["rows"], [[4]])

    def test_multiple_statements_rejected(self):
        with self.assertRaises(QueryError):
            run_select(self.conn, "SELECT 1; DROP TABLE singer")

    def test_row_cap_and_empty(self):
        r = run_select(self.conn, "SELECT * FROM singer", max_rows=2)
        self.assertEqual((len(r["rows"]), r["truncated"]), (2, True))
        with self.assertRaises(QueryError):
            run_select(self.conn, "  ;  ")

    def test_runaway_query_is_interrupted(self):
        with self.assertRaises(QueryError) as cm:
            run_select(self.conn, "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM r) SELECT COUNT(*) FROM r", timeout_s=0.3)
        self.assertIn("too long", str(cm.exception))

    def test_database_file_is_read_only_even_without_the_authorizer(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.conn.execute("DELETE FROM singer")

    def test_bad_sql_message_is_returned(self):
        with self.assertRaises(QueryError) as cm:
            run_select(self.conn, "SELECT nope FROM singer")
        self.assertIn("no such column", str(cm.exception))

    def test_schema_description(self):
        text = describe(self.conn, sample_rows=2)
        self.assertIn("CREATE TABLE singer", text)
        self.assertIn("Ada | Nigeria", text)
        self.assertNotIn("sample rows", describe(self.conn, sample_rows=0))
        self.assertLess(len(describe(self.conn, max_chars=50)), 120)


if __name__ == "__main__":
    unittest.main()
