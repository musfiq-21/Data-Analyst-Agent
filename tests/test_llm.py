"""Ollama wrapper: request shape and token counting, tested with a fake transport."""
import unittest

from sqlagent.llm import OllamaChat, parse_response


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


if __name__ == "__main__":
    unittest.main()
