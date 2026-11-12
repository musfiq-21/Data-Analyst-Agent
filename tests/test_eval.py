"""Answer checking and pass@1 / pass^k arithmetic. Written before evaluate.py."""
import unittest

from sqlagent.evaluate import (check_answer, classify_failure, pass_at_1, pass_hat_k,
                               supported_by_tools)


class CheckAnswerTests(unittest.TestCase):
    def test_number_found_in_sentence(self):
        exp = {"kind": "number", "value": 1234.5}
        self.assertTrue(check_answer(exp, "Customer 12 spent 1,234.50 in total."))
        self.assertTrue(check_answer(exp, "Total: $1234.5"))
        self.assertFalse(check_answer(exp, "Customer 12 spent 999 in total."))

    def test_number_tolerates_rounding(self):
        self.assertTrue(check_answer({"kind": "number", "value": 245.6712}, "about 245.67%"))
        self.assertFalse(check_answer({"kind": "number", "value": 245.67}, "250"))

    def test_wrong_method_near_miss_fails(self):
        # list prices instead of paid prices: 42.147% vs the true 42.0993%
        self.assertFalse(check_answer({"kind": "number", "value": 42.0993}, "42.15%"))
        self.assertTrue(check_answer({"kind": "number", "value": 42.0993}, "42.1%"))
        self.assertTrue(check_answer({"kind": "number", "value": 5655.3806}, "about 5655%"))

    def test_negative_numbers(self):
        self.assertTrue(check_answer({"kind": "number", "value": -12.5}, "growth was -12.5%"))

    def test_multiple_numbers_all_required(self):
        exp = {"kind": "numbers", "value": [300.0, 25.0]}
        self.assertTrue(check_answer(exp, "Average is 300.00, which is 25% above the minimum."))
        self.assertFalse(check_answer(exp, "Average is 300.00."))

    def test_text(self):
        exp = {"kind": "text", "value": "Electronics"}
        self.assertTrue(check_answer(exp, "The top category is electronics."))
        self.assertFalse(check_answer(exp, "The top category is Books."))

    def test_unanswerable(self):
        exp = {"kind": "unanswerable"}
        self.assertTrue(check_answer(exp, "CANNOT ANSWER: there is no phone column."))
        self.assertTrue(check_answer(exp, "The database does not contain phone numbers."))
        self.assertFalse(check_answer(exp, "The phone number is 555-1234."))

    def test_none_answer_fails(self):
        self.assertFalse(check_answer({"kind": "number", "value": 1}, None))


class SupportedByToolsTests(unittest.TestCase):
    """A pass also needs the expected value to be in a tool result: the answer must
    be *based on* what the tools returned, not a lucky guess near it."""

    def run_with(self, *results):
        return {"tool_calls": [{"name": "run_sql", "result": r} for r in results]}

    def test_number_in_tool_result(self):
        run = self.run_with({"ok": True, "rows": [[517.98425]]})
        self.assertTrue(supported_by_tools({"kind": "number", "value": 517.9842}, run))

    def test_number_close_by_luck_is_not_supported(self):
        # list-price method gave 44975 / 106710; the 42.11% was typed by the model
        run = self.run_with({"ok": True, "rows": [[106710.0, 44975.0]]})
        self.assertFalse(supported_by_tools({"kind": "number", "value": 42.0993}, run))

    def test_calculator_result_counts(self):
        run = {"tool_calls": [{"name": "calculator", "result": {"ok": True, "result": 42.0993}}]}
        self.assertTrue(supported_by_tools({"kind": "number", "value": 42.0993}, run))

    def test_numbers_all_needed(self):
        run = self.run_with({"ok": True, "rows": [[517.98, 9.0]]})
        self.assertFalse(supported_by_tools({"kind": "numbers", "value": [517.98, 5655.38]}, run))

    def test_text_must_appear_in_a_result(self):
        exp = {"kind": "text", "value": "Ceramic Mug"}
        self.assertTrue(supported_by_tools(exp, self.run_with({"ok": True, "rows": [["Ceramic Mug", 112]]})))
        self.assertFalse(supported_by_tools(exp, self.run_with({"ok": True, "rows": [[15, 112]]})))

    def test_unanswerable_needs_no_tool(self):
        self.assertTrue(supported_by_tools({"kind": "unanswerable"}, {"tool_calls": []}))

    def test_duplicate_results_count(self):
        run = self.run_with({"ok": True, "rows": [[61]], "note": "already", "duplicate_of_step": 1})
        self.assertTrue(supported_by_tools({"kind": "number", "value": 61}, run))


class MetricTests(unittest.TestCase):
    def test_pass_at_1_and_pass_hat_k(self):
        results = {"q1": [True, True, True], "q2": [True, False, True], "q3": [False] * 3}
        self.assertAlmostEqual(pass_at_1(results), 5 / 9)
        self.assertAlmostEqual(pass_hat_k(results), 1 / 3)


class FailureTests(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(classify_failure({"status": "step_cap"}, "number"), "step_cap")
        self.assertEqual(classify_failure({"status": "ok", "used_tools": False,
                                           "final_answer": "50"}, "number"), "no_tool_guess")
        self.assertEqual(classify_failure({"status": "ok", "used_tools": True,
                                           "final_answer": "The phone is 555"},
                                          "unanswerable"), "made_up_answer")
        self.assertEqual(classify_failure({"status": "ok", "used_tools": True,
                                           "final_answer": "CANNOT ANSWER"},
                                          "number"), "false_refusal")
        run = {"status": "ok", "used_tools": True, "final_answer": "12",
               "tool_calls": [{"name": "run_sql", "result": {"ok": False, "error": "x"}}]}
        self.assertEqual(classify_failure(run, "number"), "sql_error_unrecovered")
        run = {"status": "ok", "used_tools": True, "final_answer": "12",
               "tool_calls": [{"name": "run_sql", "result": {"ok": True}}]}
        self.assertEqual(classify_failure(run, "number"), "wrong_result")


if __name__ == "__main__":
    unittest.main()
