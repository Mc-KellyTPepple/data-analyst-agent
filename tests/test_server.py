import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from analyst.llm import LLMError
from analyst.server import App, Limits, csv_to_db, make_handler
from analyst.sqlguard import open_readonly, run_select
from tests.helpers import make_db

CSV = "Product Name,Units Sold,Price ($),Region\nWidget,10,2.50,North\nGadget,3,\"1,000.00\",South\nGizmo,,4,North\n"


def fake_runner(llm, question, conn, cfg):
    if "boom" in question:
        raise RuntimeError("secret internals")
    if "quota" in question:
        raise LLMError("daily quota exhausted")
    if "clarify" in question:
        return {"text": "CLARIFY: which one?", "clarify": "which one?", "final_sql": None, "trace": [], "model": "fake"}
    return {"text": "Four.\nFINAL SQL:\nSELECT COUNT(*) FROM singer", "clarify": None, "final_sql": "SELECT COUNT(*) FROM singer",
            "trace": [{"sql": "SELECT 1", "ok": True, "out": ""}], "model": "fake"}


class CsvTests(unittest.TestCase):
    def test_types_names_and_values(self):
        conn, table, cols, n = csv_to_db("sales data.csv", CSV)
        self.assertEqual((table, cols, n), ("sales_data", ["product_name", "units_sold", "price", "region"], 3))
        types = {r[1]: r[2] for r in conn.execute("PRAGMA table_info(sales_data)")}
        self.assertEqual((types["units_sold"], types["price"], types["region"]), ("INTEGER", "REAL", "TEXT"))
        self.assertEqual(conn.execute("SELECT price FROM sales_data WHERE product_name='Gadget'").fetchone()[0], 1000.0)
        self.assertIsNone(conn.execute("SELECT units_sold FROM sales_data WHERE product_name='Gizmo'").fetchone()[0])

    def test_uploaded_data_is_protected_from_writes(self):
        conn, *_ = csv_to_db("t.csv", CSV)
        for sql in ("DELETE FROM t", "DROP TABLE t", "PRAGMA query_only = OFF"):
            with self.assertRaises(Exception):
                run_select(conn, sql)
        self.assertEqual(run_select(conn, "SELECT COUNT(*) FROM t")["rows"], [[3]])

    def test_bad_csvs(self):
        for text in ("", "a,b\n", "x\n" + "1\n" * 6000, ",".join(f"c{i}" for i in range(40)) + "\n" + ",".join("1" * 40) + "\n"):
            with self.assertRaises(ValueError):
                csv_to_db("t.csv", text)
        with self.assertRaises(ValueError):
            csv_to_db("t.csv", "a\n" + "x" * 500_000)

    def test_duplicate_and_numeric_headers(self):
        _, _, cols, _ = csv_to_db("t.csv", "name,name,2024,\nx,y,1,2\n")
        self.assertEqual(cols, ["name", "name_2", "c_2024", "col4"])


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.demo = open_readonly(make_db(Path(cls.tmp.name) / "d.sqlite"), shared=True)
        cls.app = App(None, cls.demo, Limits(per_ip_per_hour=4, daily_cap=50), max_len=100, runner=fake_runner)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(cls.app))
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.demo.close()
        cls.tmp.cleanup()

    def call(self, path, body=None, ip="9.9.9.1", method=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.url + path, data=data, method=method, headers={"Content-Type": "application/json", "X-Forwarded-For": ip})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, (json.loads(r.read() or b"{}")), r.headers
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}"), e.headers

    def test_health_datasets_and_cors(self):
        code, body, headers = self.call("/health")
        self.assertEqual((code, body["ok"], headers["Access-Control-Allow-Origin"]), (200, True, "*"))
        self.assertEqual(self.call("/datasets")[1]["datasets"][0]["id"], "demo")
        code, _, headers = self.call("/ask", method="OPTIONS")
        self.assertEqual(code, 204)
        self.assertIn("POST", headers["Access-Control-Allow-Methods"])
        self.assertEqual(self.call("/nope")[0], 404)

    def test_ask_demo_and_shape(self):
        code, body, _ = self.call("/ask", {"question": "How many singers?"}, ip="9.9.9.2")
        self.assertEqual(code, 200)
        self.assertEqual((body["sql"], body["result"]["rows"], body["model"]), ("SELECT COUNT(*) FROM singer", [[4]], "fake"))

    def test_clarifying_question_passes_through(self):
        body = self.call("/ask", {"question": "clarify me"}, ip="9.9.9.3")[1]
        self.assertEqual((body["clarify"], body["sql"], body["result"]), ("which one?", None, None))

    def test_upload_then_ask_and_unknown_dataset(self):
        code, up, _ = self.call("/upload", {"name": "sales.csv", "csv": CSV}, ip="9.9.9.4")
        self.assertEqual((code, up["table"], up["rows"]), (200, "sales", 3))
        self.assertEqual(self.call("/ask", {"question": "q", "dataset": "missing"}, ip="9.9.9.4")[0], 404)
        self.assertEqual(self.call("/upload", {"name": "x.csv", "csv": "a\n"}, ip="9.9.9.4")[0], 400)

    def test_bad_inputs(self):
        self.assertEqual(self.call("/ask", {"question": ""}, ip="9.9.9.5")[0], 400)
        self.assertEqual(self.call("/ask", {"question": "x" * 101}, ip="9.9.9.5")[0], 400)
        self.assertEqual(self.call("/ask", raw=b"not json", ip="9.9.9.5")[0], 400)
        self.assertEqual(self.call("/ask", {"question": 5}, ip="9.9.9.5")[0], 400)
        self.assertEqual(self.call("/ask", raw=b"x" * 5000, ip="9.9.9.5")[0], 413)

    def test_errors_do_not_leak(self):
        code, body, _ = self.call("/ask", {"question": "boom"}, ip="9.9.9.6")
        self.assertEqual(code, 502)
        self.assertNotIn("secret", json.dumps(body))
        code, body, _ = self.call("/ask", {"question": "quota"}, ip="9.9.9.6")
        self.assertEqual(code, 503)
        self.assertIn("free quota", body["error"])

    def test_rate_limit_per_forwarded_ip(self):
        codes = [self.call("/ask", {"question": "q"}, ip="9.9.9.7")[0] for _ in range(5)]
        self.assertEqual(codes, [200, 200, 200, 200, 429])
        self.assertEqual(self.call("/ask", {"question": "q"}, ip="9.9.9.8")[0], 200)


if __name__ == "__main__":
    unittest.main()
