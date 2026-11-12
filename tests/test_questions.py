"""The question set meets the assignment's coverage rules and every expected
answer is computable from the seeded database, with no ties for 'which is top'."""
import os
import re
import sqlite3
import tempfile
import unittest
from collections import Counter

from sqlagent.build_db import build
from sqlagent.evaluate import compute_expected, load_questions


class QuestionSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "shop.db")
        build(cls.db)
        cls.questions = load_questions()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_coverage(self):
        cats = Counter(q["category"] for q in self.questions)
        self.assertGreaterEqual(len(self.questions), 15)
        for cat in ["simple", "join", "grouping", "calculation"]:
            self.assertGreaterEqual(cats[cat], 1, cat)
        self.assertGreaterEqual(cats["multi_tool"], 2)
        self.assertGreaterEqual(cats["unanswerable"], 2)

    def test_ids_unique(self):
        ids = [q["id"] for q in self.questions]
        self.assertEqual(len(ids), len(set(ids)))

    def test_expected_answers_compute(self):
        exp = compute_expected(self.questions, self.db)
        for q in self.questions:
            with self.subTest(q=q["id"]):
                if q["kind"] != "unanswerable":
                    self.assertIsNotNone(exp[q["id"]]["value"])

    def test_expected_answers_work_from_awkward_paths(self):
        # a clone under e.g. "C:\My #1 Folder" must still work
        odd_dir = os.path.join(self.tmp.name, "my #1 folder %20")
        os.makedirs(odd_dir)
        db = os.path.join(odd_dir, "shop.db")
        build(db)
        self.assertEqual(compute_expected(self.questions, db),
                         compute_expected(self.questions, self.db))

    def test_top_answers_have_no_ties(self):
        conn = sqlite3.connect(self.db)
        try:
            for q in self.questions:
                if q["kind"] != "text":
                    continue
                with self.subTest(q=q["id"]):
                    sql = re.sub(r"LIMIT 1\s*$", "LIMIT 2", q["expected_sql"])
                    # re-select the ORDER BY expression to compare the top two
                    order_expr = re.search(r"ORDER BY (.+?) DESC", sql).group(1)
                    sql = sql.replace("SELECT ", f"SELECT {order_expr} AS _k, ", 1)
                    rows = conn.execute(sql).fetchall()
                    self.assertNotEqual(rows[0][0], rows[1][0], "tie at the top")
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
