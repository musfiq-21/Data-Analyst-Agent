"""Safe calculator tool. Written before the calculator in tools.py."""
import unittest

from sqlagent.tools import calculator


class CalculatorTests(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertEqual(calculator("2 + 3 * 4")["result"], 14)
        self.assertAlmostEqual(calculator("(1250 - 1000) / 1000 * 100")["result"], 25.0)

    def test_allowed_functions(self):
        self.assertEqual(calculator("round(3.14159, 2)")["result"], 3.14)
        self.assertEqual(calculator("max(3, 9, 4)")["result"], 9)
        self.assertEqual(calculator("abs(-5)")["result"], 5)

    def test_division_by_zero_is_an_error(self):
        res = calculator("1 / 0")
        self.assertFalse(res["ok"])

    def test_rejects_names_and_attribute_access(self):
        for expr in ["__import__('os').system('echo hi')", "open('x')", "x + 1",
                     "(1).__class__", "[1, 2]"]:
            with self.subTest(expr=expr):
                self.assertFalse(calculator(expr)["ok"])

    def test_rejects_huge_power(self):
        self.assertFalse(calculator("10 ** 100000")["ok"])


if __name__ == "__main__":
    unittest.main()
