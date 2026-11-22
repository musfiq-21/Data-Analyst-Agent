"""The hand-written agent loop: tools, step cap, budget cap, statuses. Written before agent.py."""
import json
import os
import tempfile
import unittest

from sqlagent.agent import Agent
from sqlagent.build_db import build
from tests.fakes import FakeLLM, call, reply


class AgentLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = os.path.join(cls.tmp.name, "shop.db")
        build(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def agent(self, replies, **kw):
        llm = FakeLLM(replies, raise_on=kw.pop("raise_on", None))
        self.slept = []
        kw.setdefault("sleep", self.slept.append)  # record backoff instead of waiting
        return Agent(llm, self.db, **kw), llm

    def test_tool_then_answer_is_ok(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT COUNT(*) AS n FROM customers")]),
            reply(content="There are 50 customers."),
        ])
        run = agent.run("How many customers are there?")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["final_answer"], "There are 50 customers.")
        self.assertEqual(run["sql"], ["SELECT COUNT(*) AS n FROM customers"])
        self.assertEqual(run["steps"], 2)
        self.assertEqual(run["total_tokens"], 30)
        self.assertTrue(run["used_tools"])

    def test_tool_result_is_sent_back_to_model(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT 41 + 1 AS x")]),
            reply(content="42"),
        ])
        agent.run("q")
        last = llm.seen[1]["messages"][-1]
        self.assertEqual(last["role"], "tool")
        self.assertIn("42", last["content"])

    def test_sql_error_is_returned_to_model_and_it_can_retry(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="SELECT nope FROM customers")]),
            reply(tool_calls=[call("run_sql", query="SELECT COUNT(*) FROM customers")]),
            reply(content="50"),
        ])
        run = agent.run("q")
        self.assertEqual(run["status"], "ok")
        self.assertIn("nope", llm.seen[1]["messages"][-1]["content"])
        self.assertFalse(run["tool_calls"][0]["result"]["ok"])
        self.assertTrue(run["tool_calls"][1]["result"]["ok"])

    def test_write_sql_is_rejected_inside_the_loop(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query="DROP TABLE customers")]),
            reply(content="I could not do that."),
        ])
        run = agent.run("q")
        self.assertIn("rejected", llm.seen[1]["messages"][-1]["content"].lower())
        self.assertEqual(run["status"], "ok")

    def test_step_cap(self):
        loop = [reply(tool_calls=[call("run_sql", query="SELECT 1")]) for _ in range(10)]
        agent, _ = self.agent(loop, max_steps=4)
        run = agent.run("q")
        self.assertEqual(run["status"], "step_cap")
        self.assertEqual(run["steps"], 4)
        self.assertIsNone(run["final_answer"])

    def test_budget_cap(self):
        big = [reply(tool_calls=[call("run_sql", query="SELECT 1")],
                     prompt_tokens=4000, completion_tokens=1000) for _ in range(5)]
        agent, _ = self.agent(big, token_budget=8000)
        run = agent.run("q")
        self.assertEqual(run["status"], "budget_cap")
        self.assertEqual(run["steps"], 2)

    def test_llm_failure_gives_error_status_after_retries(self):
        agent, llm = self.agent([], raise_on=range(10), max_retries=3)
        run = agent.run("q")
        self.assertEqual(run["status"], "error")
        self.assertIn("ollama", run["error"])
        self.assertEqual(len(llm.seen), 4)  # first try + 3 retries
        self.assertEqual(run["retries"], 3)

    def test_failed_call_is_retried_and_run_recovers(self):
        agent, llm = self.agent([reply(content="CANNOT ANSWER: no phone column")],
                                raise_on={0, 1})
        run = agent.run("q")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["retries"], 2)
        self.assertIsNone(run["error"])

    def test_cost_cap_stops_the_run(self):
        # each step: 4000 in x $2/M + 1000 out x $8/M = $0.016
        big = [reply(tool_calls=[call("run_sql", query="SELECT 1")],
                     prompt_tokens=4000, completion_tokens=1000) for _ in range(5)]
        agent, _ = self.agent(big, cost_cap_usd=0.03, token_budget=10**6)
        run = agent.run("q")
        self.assertEqual(run["status"], "cost_cap")
        self.assertEqual(run["steps"], 2)
        self.assertAlmostEqual(run["cost_usd"], 0.032)

    def test_cost_is_recorded_without_a_cap(self):
        agent, _ = self.agent([reply(content="CANNOT ANSWER", prompt_tokens=1000,
                                     completion_tokens=100)])
        self.assertAlmostEqual(agent.run("q")["cost_usd"], 0.0028)

    def test_retry_backs_off_exponentially(self):
        agent, _ = self.agent([reply(content="CANNOT ANSWER")], raise_on={0, 1, 2},
                              retry_backoff=1.0)
        run = agent.run("q")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(self.slept, [1.0, 2.0, 4.0])
        self.assertEqual(run["retry_reasons"], ["model_call:ConnectionError"] * 3)

    def test_permanent_error_is_not_retried(self):
        from sqlagent.llm import OllamaHTTPError

        class NotFound(FakeLLM):
            def chat(self, messages, tools=None):
                self.seen.append(messages)
                raise OllamaHTTPError(404, "model 'qwen9' not found")

        llm = NotFound([])
        run = Agent(llm, self.db, sleep=self.no_sleep).run("q")
        self.assertEqual(run["status"], "error")
        self.assertEqual(len(llm.seen), 1)  # retrying a 404 cannot help
        self.assertEqual(run["retries"], 0)

    def test_server_error_is_retried(self):
        from sqlagent.agent import is_retryable
        from sqlagent.llm import OllamaHTTPError
        self.assertTrue(is_retryable(OllamaHTTPError(503, "busy")))
        self.assertTrue(is_retryable(OllamaHTTPError(429, "slow down")))
        self.assertTrue(is_retryable(TimeoutError()))
        self.assertFalse(is_retryable(OllamaHTTPError(400, "bad request")))
        self.assertFalse(is_retryable(ValueError("bug")))

    def test_truncated_reply_is_retried_not_accepted(self):
        cut = reply(content="The total revenue is 12")
        cut.done_reason = "length"
        agent, llm = self.agent([cut, reply(content="CANNOT ANSWER")])
        run = agent.run("q")
        self.assertEqual(run["final_answer"], "CANNOT ANSWER")
        self.assertEqual(run["retry_reasons"], ["truncated"])
        self.assertIn("cut off", llm.seen[1]["messages"][-1]["content"])

    def test_invalid_arguments_are_rejected_with_the_schema(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("run_sql", query=42)]),
            reply(tool_calls=[call("calculator")]),
            reply(content="CANNOT ANSWER"),
        ])
        run = agent.run("q")
        first = json.loads(llm.seen[1]["messages"][-1]["content"])["error"]
        self.assertIn("$.query must be string", first)
        self.assertIn('"query"', first)  # the schema to follow is in the message
        self.assertIn("missing required field 'expression'",
                      llm.seen[2]["messages"][-1]["content"])
        self.assertEqual(run["invalid_tool_calls"], 2)
        self.assertEqual(run["retry_reasons"], ["invalid_tool_call"] * 2)
        self.assertEqual(run["sql"], [])  # the bad query never reached the database

    def test_invalid_calls_stop_at_retry_cap(self):
        bad = [reply(tool_calls=[call("calculator", expression="")]) for _ in range(6)]
        agent, _ = self.agent(bad, max_retries=2, guards=False)
        run = agent.run("q")
        self.assertEqual(run["status"], "retry_cap")
        self.assertEqual(run["retries"], 2)

    def test_sql_alias_is_accepted(self):
        agent, _ = self.agent([reply(tool_calls=[call("run_sql", sql="SELECT 1")]),
                               reply(content="1")])
        self.assertEqual(agent.run("q")["sql"], ["SELECT 1"])

    def test_malformed_tool_output_is_caught(self):
        agent, llm = self.agent([reply(tool_calls=[call("calculator", expression="1+1")]),
                                 reply(content="CANNOT ANSWER")])
        broken = agent.registry["calculator"]
        from dataclasses import replace
        agent.registry["calculator"] = replace(broken, fn=lambda a: {"ok": True})
        run = agent.run("q")
        self.assertEqual(run["tool_output_errors"], 1)
        self.assertIn("malformed output", llm.seen[1]["messages"][-1]["content"])

    def no_sleep(self, *_):
        raise AssertionError("should not sleep")

    def test_empty_replies_stop_at_retry_cap(self):
        agent, _ = self.agent([reply(content="") for _ in range(6)], max_retries=3)
        run = agent.run("q")
        self.assertEqual(run["status"], "retry_cap")
        self.assertEqual(run["empty_replies"], 4)

    def test_unknown_tool_is_reported_back(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("delete_everything")]),
            reply(content="ok then"),
        ])
        agent.run("q")
        self.assertIn("unknown tool", llm.seen[1]["messages"][-1]["content"].lower())

    def test_json_tool_call_written_as_text_is_parsed(self):
        agent, _ = self.agent([
            reply(content='{"name": "run_sql", "arguments": {"query": "SELECT 1"}}'),
            reply(content="1"),
        ])
        run = agent.run("q")
        self.assertEqual(run["sql"], ["SELECT 1"])

    def test_numeric_answer_without_tools_gets_one_nudge(self):
        agent, llm = self.agent([
            reply(content="There are about 50 customers."),
            reply(tool_calls=[call("run_sql", query="SELECT COUNT(*) FROM customers")]),
            reply(content="50"),
        ])
        run = agent.run("q")
        self.assertIn("tool", llm.seen[1]["messages"][-1]["content"].lower())
        self.assertTrue(run["used_tools"])
        self.assertEqual(run["final_answer"], "50")

    def test_empty_reply_is_retried_not_accepted(self):
        # Ollama drops a malformed tool call and returns an empty message
        agent, llm = self.agent([
            reply(content=""),
            reply(tool_calls=[call("run_sql", query="SELECT 1")]),
            reply(content="1"),
        ])
        run = agent.run("q")
        self.assertEqual(run["status"], "ok")
        self.assertEqual(run["final_answer"], "1")
        self.assertEqual(run["empty_replies"], 1)
        self.assertIn("empty", llm.seen[1]["messages"][-1]["content"].lower())

    def test_only_empty_replies_end_at_step_cap(self):
        agent, _ = self.agent([reply(content="") for _ in range(3)], max_steps=3)
        run = agent.run("q")
        self.assertEqual(run["status"], "step_cap")
        self.assertIsNone(run["final_answer"])

    def test_refusal_without_tools_is_accepted(self):
        agent, _ = self.agent([reply(content="CANNOT ANSWER: there is no phone column.")])
        run = agent.run("What is customer 5's phone number?")
        self.assertEqual(run["status"], "ok")
        self.assertFalse(run["used_tools"])

    def test_calculator_tool(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("calculator", expression="(120 - 100) / 100 * 100")]),
            reply(content="20%"),
        ])
        agent.run("q")
        self.assertIn("20", llm.seen[1]["messages"][-1]["content"])

    def test_inline_mode_puts_schema_in_system_prompt(self):
        agent, llm = self.agent([reply(content="CANNOT ANSWER")], sql_mode="inline")
        agent.run("q")
        system = llm.seen[0]["messages"][0]["content"]
        self.assertIn("unit_price", system)
        tool_names = {t["function"]["name"] for t in llm.seen[0]["tools"]}
        self.assertNotIn("generate_sql", tool_names)

    def test_tool_mode_generate_sql_makes_its_own_model_call(self):
        agent, llm = self.agent([
            reply(tool_calls=[call("generate_sql", question="count customers")]),
            reply(content="```sql\nSELECT COUNT(*) FROM customers;\n```", prompt_tokens=100),
            reply(tool_calls=[call("run_sql", query="SELECT COUNT(*) FROM customers")]),
            reply(content="50"),
        ], sql_mode="tool")
        run = agent.run("How many customers?")
        self.assertEqual(run["status"], "ok")
        # the inner SQL-writer call saw the full schema
        self.assertIn("unit_price", llm.seen[1]["messages"][0]["content"])
        # the agent got clean SQL back
        self.assertIn("SELECT COUNT(*) FROM customers", llm.seen[2]["messages"][-1]["content"])
        self.assertNotIn("```", llm.seen[2]["messages"][-1]["content"])
        # inner tokens count toward the run
        self.assertEqual(run["total_tokens"], 15 + 105 + 15 + 15)
        self.assertIn("SELECT COUNT(*) FROM customers", run["sql"])

    def test_run_record_has_required_fields(self):
        agent, _ = self.agent([reply(content="CANNOT ANSWER")])
        run = agent.run("q")
        for key in ["question", "status", "final_answer", "tool_calls", "sql", "steps",
                    "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd",
                    "retries", "seconds", "model"]:
            self.assertIn(key, run)


if __name__ == "__main__":
    unittest.main()
