"""Ollama wrapper: request shape and token counting, tested with a fake transport."""
import unittest

from sqlagent.llm import OllamaChat, notional_cost, parse_response


class ParseTests(unittest.TestCase):
    def test_reads_token_counts(self):
        r = parse_response({"message": {"role": "assistant", "content": "hi"},
                            "prompt_eval_count": 152, "eval_count": 25})
        self.assertEqual((r.prompt_tokens, r.completion_tokens), (152, 25))
        self.assertEqual(r.content, "hi")
        self.assertEqual(r.tool_calls, [])

    def test_missing_counts_default_to_zero(self):
        r = parse_response({"message": {"content": ""}})
        self.assertEqual((r.prompt_tokens, r.completion_tokens), (0, 0))

    def test_tool_calls_normalised(self):
        r = parse_response({"message": {"content": "", "tool_calls": [
            {"function": {"name": "calculator", "arguments": {"expression": "1+1"}}},
            {"function": {"name": "run_sql", "arguments": '{"query": "SELECT 1"}'}},
        ]}})
        self.assertEqual(r.tool_calls, [
            {"name": "calculator", "arguments": {"expression": "1+1"}},
            {"name": "run_sql", "arguments": {"query": "SELECT 1"}},
        ])


class ClientTests(unittest.TestCase):
    def test_payload_and_ledger(self):
        sent = []

        def transport(url, payload, timeout):
            sent.append((url, payload))
            return {"message": {"content": "ok"}, "prompt_eval_count": 7, "eval_count": 3}

        llm = OllamaChat("qwen2.5:3b", temperature=0.2, num_ctx=8192, transport=transport)
        llm.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
        llm.chat([{"role": "user", "content": "y"}])
        url, payload = sent[0]
        self.assertTrue(url.endswith("/api/chat"))
        self.assertEqual(payload["model"], "qwen2.5:3b")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["options"]["temperature"], 0.2)
        self.assertEqual(payload["options"]["num_ctx"], 8192)
        self.assertIn("tools", payload)
        self.assertNotIn("tools", sent[1][1])
        self.assertEqual(len(llm.ledger), 2)
        self.assertEqual(llm.total_tokens(), 20)


    def test_format_passed_and_cost_recorded(self):
        sent = []

        def transport(url, payload, timeout):
            sent.append(payload)
            return {"message": {"content": "{}"}, "prompt_eval_count": 1000,
                    "eval_count": 100, "done_reason": "length"}

        llm = OllamaChat("qwen2.5:3b", transport=transport)
        schema = {"type": "object"}
        r = llm.chat([{"role": "user", "content": "x"}], format=schema)
        llm.chat([{"role": "user", "content": "y"}])
        self.assertEqual(sent[0]["format"], schema)
        self.assertNotIn("format", sent[1])
        self.assertEqual(r.done_reason, "length")
        # 1000 in x $2/M + 100 out x $8/M = $0.0028
        self.assertAlmostEqual(llm.ledger[0]["cost_usd"], 0.0028)
        self.assertAlmostEqual(llm.total_cost(), 0.0056)

    def test_slide_cost_example(self):
        # Lecture 2 slide 20, no-cache row: 8,000 in + 400 out = $0.0192
        self.assertAlmostEqual(notional_cost(8000, 400), 0.0192)
        # prefix-hit row: 2,000 new + 6,000 cached input + 400 out = $0.0102
        self.assertAlmostEqual(notional_cost(8000, 400, cached_tokens=6000), 0.0102)


if __name__ == "__main__":
    unittest.main()
