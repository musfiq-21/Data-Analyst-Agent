"""Read-only safety of the SQL executor. Written before sqlagent/tools.py."""
import os
import sqlite3
import tempfile
import unittest

from sqlagent.build_db import build
from sqlagent.tools import check_sql, open_readonly, run_sql


class GuardTests(unittest.TestCase):
    def test_accepts_plain_select(self):
        self.assertIsNone(check_sql("SELECT COUNT(*) FROM customers"))

    def test_accepts_lowercase_and_trailing_semicolon(self):
        self.assertIsNone(check_sql("  select * from products;  "))

    def test_accepts_with_cte(self):
        self.assertIsNone(check_sql("WITH t AS (SELECT 1 AS x) SELECT x FROM t"))

    def test_accepts_column_names_containing_keywords(self):
        # created_at / updated_flag must not trip the CREATE / UPDATE word check
        self.assertIsNone(check_sql("SELECT created_at, updated_flag FROM t"))

    def test_accepts_replace_string_function(self):
        # replace() is a read-only string function; only REPLACE INTO is a write
        self.assertIsNone(check_sql("SELECT replace(name, 'a', 'b') FROM customers"))

    def test_rejects_write_statements(self):
        for sql in [
            "DROP TABLE customers",
            "DELETE FROM orders",
            "UPDATE products SET unit_price = 0",
            "INSERT INTO customers VALUES (1, 'x', 'y', '2024-01-01')",
            "CREATE TABLE x (a)",
            "ALTER TABLE customers ADD COLUMN z",
            "PRAGMA writable_schema = 1",
            "ATTACH DATABASE 'x.db' AS x",
            "REPLACE INTO customers VALUES (1)",
            "VACUUM",
        ]:
            with self.subTest(sql=sql):
                self.assertIsNotNone(check_sql(sql))

    def test_rejects_multiple_statements(self):
        self.assertIsNotNone(check_sql("SELECT 1; DROP TABLE customers"))
        self.assertIsNotNone(check_sql("SELECT 1; SELECT 2"))

    def test_rejects_write_hidden_inside_select(self):
        self.assertIsNotNone(check_sql("SELECT * FROM (DELETE FROM orders)"))

    def test_rejects_empty(self):
        self.assertIsNotNone(check_sql(""))
        self.assertIsNotNone(check_sql("   ;  "))


class ExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "shop.db")
        build(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_select_returns_rows(self):
        res = run_sql(self.db, "SELECT COUNT(*) AS n FROM customers")
        self.assertTrue(res["ok"])
        self.assertEqual(res["columns"], ["n"])
        self.assertEqual(len(res["rows"]), 1)

    def test_rejected_sql_returns_error_not_exception(self):
        res = run_sql(self.db, "DROP TABLE customers")
        self.assertFalse(res["ok"])
        self.assertIn("rejected", res["error"].lower())
        # table still there
        self.assertTrue(run_sql(self.db, "SELECT 1 FROM customers LIMIT 1")["ok"])

    def test_bad_sql_returns_database_error_text(self):
        res = run_sql(self.db, "SELECT no_such_column FROM customers")
        self.assertFalse(res["ok"])
        self.assertIn("no_such_column", res["error"])

    def test_row_limit_applied(self):
        res = run_sql(self.db, "SELECT * FROM order_items", row_limit=10)
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["rows"]), 10)
        self.assertTrue(res["truncated"])

    def test_small_result_not_truncated(self):
        res = run_sql(self.db, "SELECT 1", row_limit=10)
        self.assertFalse(res["truncated"])

    def test_readonly_connection_refuses_writes_even_without_guard(self):
        conn = open_readonly(self.db)
        try:
            with self.assertRaises(sqlite3.Error):
                conn.execute("DELETE FROM customers")
        finally:
            conn.close()

    def test_missing_database_is_an_error(self):
        res = run_sql(os.path.join(self.tmp.name, "nope.db"), "SELECT 1")
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
