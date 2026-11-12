# AI-assistant use log

The course requires three lines per piece: what the assistant wrote, which test proves it,
and what I reviewed. The third column is for **me** to fill in after reading the code.

Assistant: Claude Code (Claude Opus 5.5). Tests were written before each module and run red
first (all 6 test modules failed on import), then the code was written to make them pass.

| # | What it wrote | Test that proves it | What I reviewed |
|---|---|---|---|
| 1 | `sqlagent/build_db.py`: seeded 4-table shop DB | `tests/test_db.py` (tables, ≥100 rows, FK integrity, same seed = same data) | |
| 2 | `sqlagent/tools.py` `check_sql` / `run_sql`: read-only guard, row limit | `tests/test_guard.py` (DROP/DELETE/UPDATE/INSERT/multi-statement rejected, ro connection) | |
| 3 | `sqlagent/tools.py` `calculator`: AST-based safe arithmetic | `tests/test_calculator.py` | |
| 4 | `sqlagent/llm.py`: Ollama client and token ledger | `tests/test_llm.py` (fake transport) | |
| 5 | `sqlagent/agent.py`: loop, step cap, budget cap, statuses, nudge, empty-reply retry, tool-mode `generate_sql` | `tests/test_agent.py` (scripted fake model) | |
| 6 | `sqlagent/evaluate.py`: checker, pass@1, pass^k, failure labels | `tests/test_eval.py` | |
| 7 | `data/questions.json`: 19 questions with expected SQL | `tests/test_questions.py` (coverage, computable, no ties) | |
| 8 | `agent.py` orchestration guards (error hints, refusal-after-error push-back, grounding check, duplicate cache, NULL/multi-row hints, prompt rules 5–7), built from a trace-by-trace analysis of the first run's 22 failures | `tests/test_guards.py` | |
| 9 | `evaluate.py` strict checker: value must appear in a tool result; tolerance tightened from 1% to 0.1% after a wrong-method 42.15% passed for a true 42.10% | `tests/test_eval.py` (`SupportedByToolsTests`, `test_wrong_method_near_miss_fails`) | |
| 10 | `REPORT.md` draft, written from the `runs/*/summary.json` and `runs.jsonl` files | No unit test: every number was checked by a script against the run files | |

Found during live testing: Ollama sometimes returns an **empty message** when qwen2.5:3b
writes a tool call it cannot parse. The first version accepted `""` as the final answer.
I added `test_empty_reply_is_retried_not_accepted` (red), then the retry in `agent.py`.
