"""Database builder: shape, size, seeded determinism. Written before build_db.py."""
import hashlib
import os
import sqlite3
import tempfile
import unittest

from sqlagent.build_db import build


def dump_hash(path):
    conn = sqlite3.connect(path)
    h = hashlib.sha256()
    for line in conn.iterdump():
        h.update(line.encode("utf-8"))
    conn.close()
    return h.hexdigest()


class BuildDbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "shop.db")
        build(self.path)
        self.conn = sqlite3.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_has_at_least_three_tables(self):
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"customers", "products", "orders", "order_items"} <= names)

    def test_biggest_table_has_100_plus_rows(self):
        n = self.conn.execute("SELECT COUNT(*) FROM order_items").fetchone()[0]
        self.assertGreaterEqual(n, 100)

    def test_foreign_keys_are_consistent(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        orphans = self.conn.execute(
            "SELECT COUNT(*) FROM order_items oi LEFT JOIN orders o ON o.id = oi.order_id "
            "WHERE o.id IS NULL").fetchone()[0]
        self.assertEqual(orphans, 0)

    def test_same_seed_gives_identical_data(self):
        other = os.path.join(self.tmp.name, "again.db")
        build(other)
        self.assertEqual(dump_hash(self.path), dump_hash(other))

    def test_different_seed_gives_different_data(self):
        other = os.path.join(self.tmp.name, "other.db")
        build(other, seed=7)
        self.assertNotEqual(dump_hash(self.path), dump_hash(other))

    def test_rebuild_overwrites_existing_file(self):
        n = self.conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        self.conn.close()  # Windows cannot replace a file that is still open
        build(self.path)  # must not fail or duplicate rows
        self.conn = sqlite3.connect(self.path)  # for tearDown
        conn2 = sqlite3.connect(self.path)
        self.assertEqual(conn2.execute("SELECT COUNT(*) FROM customers").fetchone()[0], n)
        conn2.close()


if __name__ == "__main__":
    unittest.main()
