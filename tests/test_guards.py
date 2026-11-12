"""Orchestration guards: the loop catches common small-model slips instead of
accepting them. Written before the guard code in agent.py."""
import os
import tempfile
import unittest

from sqlagent.agent import Agent, ungrounded_numbers
from sqlagent.build_db import build
from tests.fakes import FakeLLM, call, reply


class GuardBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "shop.db")
        build(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def agent(self, replies, **kw):
        llm = FakeLLM(replies)
        return Agent(llm, self.db, **kw), llm


class ErrorHintTests(GuardBase):
    def test_missing_column_hint_names_the_table_that_has_it(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT SUM(quantity) FROM orders")]),
            reply(content="CANNOT ANSWER: x"), reply(content="CANNOT ANSWER: x"),
            reply(content="CANNOT ANSWER: x"), reply(content="CANNOT ANSWER: x"),
        ])
        agent.run("q")
        tool_msg = llm.seen[1]["messages"][-1]["content"]
        self.assertIn("order_items", tool_msg)
        self.assertIn("not missing data", tool_msg.lower())

    def test_qualified_column_from_table_not_in_query_hint(self):
        # order_items.unit_price exists, but order_items is not in the FROM clause
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql",
                                   query="SELECT AVG(order_items.unit_price) FROM orders")]),
            reply(content="done"),
        ])
        agent.run("q")
        msg = llm.seen[1]["messages"][-1]["content"].lower()
        self.assertIn("not in the from", msg)
        self.assertIn("order_items", msg)

    def test_id_only_result_hints_to_fetch_the_name(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query=(
                "SELECT product_id, SUM(quantity) AS units FROM order_items "
                "GROUP BY product_id ORDER BY units DESC LIMIT 1"))]),
            reply(content="done"),
        ])
        agent.run("Which product sold the most?")
        msg = llm.seen[1]["messages"][-1]["content"].lower()
        self.assertIn("id", msg)
        self.assertIn("name", msg)

    def test_result_with_name_column_gets_no_id_hint(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT id, name FROM customers LIMIT 1")]),
            reply(content="done"),
        ])
        agent.run("q")
        self.assertNotIn("hint", llm.seen[1]["messages"][-1]["content"].lower())

    def test_unknown_column_anywhere_says_so(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT phone FROM customers")]),
            reply(content="CANNOT ANSWER: there is no phone column."),
        ])
        run = agent.run("q")
        self.assertIn("no table has a column named 'phone'",
                      llm.seen[1]["messages"][-1]["content"].lower())
        # the column truly does not exist, so refusing is accepted without push-back
        self.assertEqual(run["final_answer"], "CANNOT ANSWER: there is no phone column.")

    def test_null_result_gets_hint(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql",
                                   query="SELECT SUM(unit_price) FROM products WHERE category='Nope'")]),
            reply(content="done"),
        ])
        agent.run("q")
        self.assertIn("matched nothing", llm.seen[1]["messages"][-1]["content"].lower())

    def test_multi_row_single_number_gets_hint(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql",
                                   query="SELECT SUM(quantity) FROM order_items GROUP BY order_id")]),
            reply(content="done"),
        ])
        agent.run("q")
        self.assertIn("rows", llm.seen[1]["messages"][-1]["content"].lower())
        self.assertIn("group by", llm.seen[1]["messages"][-1]["content"].lower())


class RefusalGuardTests(GuardBase):
    def test_refusal_right_after_failed_query_is_pushed_back(self):
        agent, llm = self.agent([
            # 'quantity' exists, but in order_items: a fixable mistake, not missing data
            reply(tool_calls=[call("run_sql", query="SELECT SUM(quantity) FROM customers")]),
            reply(content="CANNOT ANSWER: column missing"),
            reply(tool_calls=[call("run_sql", query="SELECT COUNT(*) FROM customers")]),
            reply(content="There are 50 customers."),
        ])
        run = agent.run("How many customers?")
        self.assertIn("refusal_after_error", run["guard_pushbacks"])
        self.assertIn("not mean", llm.seen[2]["messages"][-1]["content"].lower())
        self.assertEqual(run["final_answer"], "There are 50 customers.")

    def test_refusal_after_successful_query_is_accepted(self):
        agent, _ = self.agent([
            reply(tool_calls=[call("describe_table", table="customers")]),
            reply(content="CANNOT ANSWER: no phone column."),
        ])
        run = agent.run("phone?")
        self.assertEqual(run["guard_pushbacks"], [])
        self.assertEqual(run["final_answer"], "CANNOT ANSWER: no phone column.")

    def test_guards_off_accepts_refusal_after_error(self):
        agent, _ = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT SUM(quantity) FROM customers")]),
            reply(content="CANNOT ANSWER: column missing"),
        ], guards=False)
        run = agent.run("q")
        self.assertEqual(run["final_answer"], "CANNOT ANSWER: column missing")


class GroundingTests(GuardBase):
    def test_ungrounded_number_is_pushed_back(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT 14.5 AS pct")]),
            reply(content="25.00% of all orders were cancelled."),
            reply(content="14.50% of all orders were cancelled."),
        ])
        run = agent.run("What percentage of orders were cancelled?")
        self.assertIn("ungrounded_numbers", run["guard_pushbacks"])
        self.assertIn("25", llm.seen[2]["messages"][-1]["content"])
        self.assertEqual(run["final_answer"], "14.50% of all orders were cancelled.")

    def test_rounded_tool_value_is_grounded(self):
        self.assertEqual(ungrounded_numbers("Average is $517.98.", [517.98425]), [])
        self.assertEqual(ungrounded_numbers("Total 1,192.00", [1192.0]), [])
        self.assertEqual(ungrounded_numbers("about 5655", [5655.3806]), [])

    def test_question_numbers_and_small_ints_are_allowed(self):
        self.assertEqual(ungrounded_numbers("Customer 12 spent 1192.", [1192.0, 12]), [])
        self.assertEqual(ungrounded_numbers("1. first 2. second", []), [])

    def test_mental_arithmetic_is_flagged(self):
        bad = ungrounded_numbers("difference = 517.98 - 9.00 = 498.98, so 5544.22%",
                                 [517.98425, 9.0])
        self.assertIn(498.98, bad)
        self.assertIn(5544.22, bad)

    def test_calculator_result_grounds_the_number(self):
        agent, _ = self.agent([
            reply(tool_calls=[call("calculator", expression="(517.98 - 9) / 9 * 100")]),
            reply(content="It is 5655.33% above the minimum."),
        ])
        run = agent.run("q")
        self.assertEqual(run["guard_pushbacks"], [])

    def test_pushbacks_are_capped(self):
        agent, _ = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT 1")]),
            reply(content="It is 999."), reply(content="It is 998."),
            reply(content="It is 997."), reply(content="It is 996."),
        ], max_pushbacks=2)
        run = agent.run("q")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["final_answer"], "It is 997.")
        self.assertEqual(len(run["guard_pushbacks"]), 2)


class DuplicateCallTests(GuardBase):
    def test_identical_call_is_not_rerun(self):
        q = "SELECT COUNT(*) FROM customers"
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query=q), call("run_sql", query=q)]),
            reply(content="50"),
        ])
        run = agent.run("q")
        self.assertEqual(run["sql"], [q])
        self.assertIn("already", llm.seen[1]["messages"][-1]["content"].lower())

    def test_refusal_after_duplicate_empty_result_is_pushed_back(self):
        q = "SELECT SUM(unit_price) FROM products WHERE category = 'Nope'"
        agent, _ = self.agent([
            reply(tool_calls=[call("run_sql", query=q)]),
            reply(tool_calls=[call("run_sql", query=q)]),   # duplicate of an empty result
            reply(content="CANNOT ANSWER: no data"),
            reply(content="CANNOT ANSWER: no data"),
            reply(content="CANNOT ANSWER: no data"),
        ])
        run = agent.run("q")
        self.assertIn("refusal_after_error", run["guard_pushbacks"])


class PromptTests(GuardBase):
    def test_guard_rules_in_prompt_only_when_on(self):
        on, llm_on = self.agent([reply(content="CANNOT ANSWER")])
        off, llm_off = self.agent([reply(content="CANNOT ANSWER")], guards=False)
        on.run("q")
        off.run("q")
        self.assertIn("not the id", llm_on.seen[0]["messages"][0]["content"].lower())
        self.assertNotIn("not the id", llm_off.seen[0]["messages"][0]["content"].lower())


if __name__ == "__main__":
    unittest.main()
