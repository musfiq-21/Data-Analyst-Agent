"""Tool contracts: every tool's input and output schema, and the validator behind them."""
import os
import tempfile
import unittest

from sqlagent.build_db import build
from sqlagent.tools import (CALCULATOR, DESCRIBE_TABLE, GENERATE_SQL, RUN_SQL, calculator,
                            describe_table, run_sql, validate)

TOOLS = [RUN_SQL, CALCULATOR, DESCRIBE_TABLE, GENERATE_SQL]


class ValidatorTests(unittest.TestCase):
    def test_types(self):
        self.assertEqual(validate(1, {"type": "integer"}), [])
        self.assertTrue(validate(True, {"type": "integer"}))  # bool is not a JSON number
        self.assertTrue(validate(1.5, {"type": "integer"}))
        self.assertEqual(validate(None, {"type": ["string", "null"]}), [])

    def test_nested_paths_in_errors(self):
        schema = {"type": "object", "properties": {"rows": {"type": "array",
                                                            "items": {"type": "array"}}}}
        self.assertEqual(validate({"rows": [[1], 2]}, schema),
                         ["$.rows[1] must be array, got int"])

    def test_any_of(self):
        schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
        self.assertEqual(validate(3, schema), [])
        self.assertTrue(validate([], schema))


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "shop.db")
        build(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_tool_spec_is_well_formed(self):
        for t in TOOLS:
            spec = t.spec()["function"]
            self.assertEqual(spec["name"], t.name)
            self.assertEqual(spec["parameters"]["type"], "object")
            for req in spec["parameters"]["required"]:
                self.assertIn(req, spec["parameters"]["properties"])

    def test_real_outputs_match_their_schemas(self):
        """The contract is checked against what the tools actually return, ok and error."""
        cases = [
            (RUN_SQL, run_sql(self.db, "SELECT COUNT(*) AS n FROM customers")),
            (RUN_SQL, run_sql(self.db, "SELECT nope FROM customers")),
            (RUN_SQL, run_sql(self.db, "DROP TABLE customers")),
            (CALCULATOR, calculator("(2 + 3) * 4")),
            (CALCULATOR, calculator("1 / 0")),
            (DESCRIBE_TABLE, describe_table(self.db)),
            (DESCRIBE_TABLE, describe_table(self.db, "orders")),
            (DESCRIBE_TABLE, describe_table(self.db, "nope")),
            (GENERATE_SQL, {"ok": True, "sql": "SELECT 1"}),
        ]
        for tool, result in cases:
            with self.subTest(tool=tool.name, result=result):
                self.assertEqual(validate(result, tool.output_schema), [])

    def test_output_schema_rejects_broken_results(self):
        self.assertTrue(validate({"ok": True, "rows": "1"}, RUN_SQL.output_schema))
        self.assertTrue(validate({"ok": False}, RUN_SQL.output_schema))  # error text missing
        self.assertTrue(validate({"ok": 1, "error": "x"}, RUN_SQL.output_schema))
        self.assertTrue(validate({"ok": True, "result": "4"}, CALCULATOR.output_schema))

    def test_input_schema_rejects_bad_arguments(self):
        self.assertEqual(validate({"query": "SELECT 1"}, RUN_SQL.input_schema), [])
        self.assertTrue(validate({}, RUN_SQL.input_schema))
        self.assertTrue(validate({"query": ""}, RUN_SQL.input_schema))
        self.assertTrue(validate({"expression": 5}, CALCULATOR.input_schema))
        self.assertEqual(validate({}, DESCRIBE_TABLE.input_schema), [])  # table is optional


if __name__ == "__main__":
    unittest.main()
