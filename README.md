# SQL Calculation Agent

An agent that answers calculation questions about a shop database by **calling tools**,
never by guessing numbers. Model: **`qwen2.5:3b`** served by Ollama. Agent loop written by
hand; Python standard library only (no pip installs).

## Run everything (5 commands)

Prerequisites: Python 3.10+, Ollama running (`ollama serve`, or the Ollama desktop app).

```bash
git clone <your-repo-url> && cd AgenticSWE/assignment1-sql-agent
ollama pull qwen2.5:3b
python -m sqlagent.build_db
python -m unittest
python -m sqlagent.evaluate
```

`build_db` rebuilds `data/shop.db` from seed 42 (identical data on every machine).
`unittest` runs 89 offline tests (no Ollama needed; the model is faked).
`evaluate` runs 19 questions × 3 trials and writes `runs/qwen2.5-3b_inline/`.

> Windows: if `python` opens the Microsoft Store, use `py` instead, or turn off the
> "App execution aliases" for python in Windows Settings.

### Other useful commands

```bash
python -m sqlagent.ask "What is the total revenue from customer 12?"
```

```bash
python -m sqlagent.evaluate --sql-mode tool
```

```bash
python -m sqlagent.evaluate --model qwen2.5:latest
```

```bash
python -m sqlagent.evaluate --no-guards --tag qwen2.5-3b_inline_no-guards
```

```bash
python -m sqlagent.evaluate --no-nudge --tag baseline-no-nudge
```

```bash
python -m sqlagent.evaluate --only q13,q15 --trials 1
```

## Layout

```
sqlagent/
  build_db.py    seeded SQLite builder: customers, products, orders, order_items
  tools.py       run_sql (read-only, guarded, row limit), calculator, describe_table
  llm.py         Ollama /api/chat client (urllib) with a per-call token ledger
  agent.py       the agent loop: step cap, token budget, final status
  evaluate.py    expected answers, answer checker, pass@1 / pass^k, failure labels
  ask.py         ask one question and print the tool trace
data/
  questions.json         19 questions, each with its own expected_sql
  expected_answers.json  expected values computed from that SQL (written by evaluate)
  shop.db                built by build_db (not committed)
runs/<model>_<mode>/
  runs.jsonl     one record per run: question, tool calls + results, SQL, answer,
                 status, tokens, seconds, passed, failure label
  summary.json   pass@1, pass^3, statuses, failure counts with one example each
  summary.md     the same as a table
tests/           unittest suite (written before the code it tests)
```

## Design

```
question ─► Agent loop (max 8 steps, 20k-token budget)
              │  system prompt = rules + definitions + full schema
              ▼
          qwen2.5:3b via Ollama /api/chat (native tools)
              │ tool call                         │ text reply
              ▼                                   ▼
   run_sql ─ guard ─ read-only SQLite      empty?     → "try again" (counts as a step)
   calculator (safe AST, no eval)          number but no tool used yet? → one nudge
   describe_table                          otherwise  → final answer, status ok
   [generate_sql in tool mode]
              │ result JSON (rows or error text)
              └──────────► appended as a `tool` message, loop continues
```

**Final status** of every run: `ok`, `step_cap`, `budget_cap` or `error`.

**Orchestration guards** (on by default; `--no-guards` turns them off to reproduce the first
version). These were added after reading every failed trace of the first run
(`runs/qwen2.5-3b_inline_no-guards/`). Each one targets a slip the loop used to accept:

| Guard | What it stops |
|---|---|
| Schema-aware error hint (`no such column: quantity` → "exists in order_items; join it"; `order_items.unit_price` → "order_items is not in the FROM clause of that subquery") | Model can't tell how to fix the query |
| ID-only result hint ("these rows have IDs but no names; join products.name") | Answering "which product" with "product ID 15" |
| Refusal right after a failed/empty query gets one push-back | Model reads its own SQL error as "the data doesn't exist" |
| Grounding check: every number in the answer must match a tool result or the question | Mental arithmetic, misread or invented numbers |
| Duplicate-call cache: an identical call isn't re-run; the model is told it already has the result | The same query repeated over and over |
| Hints for NULL/empty results and for multi-row results to single-number questions | Giving up on an empty result; reporting only the first of several rows |
| Prompt rules 5–7: an error ≠ missing data; don't substitute a different measure; names, not IDs | The same, at the prompt level |

Iterations: `runs/qwen2.5-3b_inline_no-guards/` (v0, no guards),
`runs/qwen2.5-3b_inline_guards-v1/` (first guard set, before the two hint fixes), and
`runs/qwen2.5-3b_inline/` (current). All are scored with the same checker (`--rescore`).

There are at most 2 push-backs per run. After that the answer is accepted as it is, and the
step cap still applies. The guards are generic (none mentions a test question), but they were
designed after seeing failures on this question set. The before/after gain is therefore
measured on the questions the guards were tuned on, which you should mention in the report.

**SQL generation: design choice.** Both options from the brief are implemented
(`--sql-mode`):

- `inline` (default): the full `CREATE TABLE` schema is in the agent's system prompt and
  the agent writes SQL as the argument of `run_sql`. The agent sees the real column names
  when it plans, and each step costs one model call.
- `tool`: the agent sees only table names and calls `generate_sql(question)`, which makes
  its own model call with a focused "write one SELECT" prompt plus the schema. Each query
  costs two model calls, and the agent can't see the columns unless it calls
  `describe_table`.

Run both and use the numbers to justify the choice in the report.

**Read-only safety.** There are three independent layers, each tested in `tests/test_guard.py`:
1. Text guard: exactly one statement, must start with `SELECT`/`WITH`, rejects
   `INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/REPLACE/ATTACH/PRAGMA/...` anywhere.
2. The connection is opened with `mode=ro` and `PRAGMA query_only = ON`.
3. A SQLite authorizer callback denies every action except reads.

At most 50 rows are returned (`truncated: true` is set when rows were cut).

**Error handling.** SQL errors, rejected queries, unknown tools and bad arguments go back
to the model as the tool result text, so it can fix the call and retry within the step cap.

**Token counting.** The counts are Ollama's own `prompt_eval_count` and `eval_count`, summed per
run, including the inner `generate_sql` calls. On Ollama 0.40.16, `prompt_eval_count` was
measured to be the full prompt length even when the prompt is cached; the cache only makes
the call faster (see Assignment 2). Every step therefore counts the whole conversation so
far, and the budget uses exactly what Ollama reports.

**Ollama settings.** `temperature 0.3` (so the 3 trials can differ; a fixed temperature of 0
would make pass@1 = pass^3 trivially), `num_ctx 8192` (the schema, tools and a few steps
do not fit Ollama's small default context, which it truncates silently),
`num_predict 512`.

## How answers are checked

- Expected values come from each question's `expected_sql`, run directly on the database,
  **not** through the agent (see `data/expected_answers.json`).
- `number`: some number in the answer is within 0.1% (or 0.01) of the expected value. That
  allows rounding to 2 decimals, but not a near miss from a wrong method: list prices give
  42.15% instead of the true 42.10% for q16, which a 1% tolerance used to pass.
  `numbers`: every expected number must appear. `text`: the expected name appears.
  `unanswerable`: the answer must decline ("CANNOT ANSWER", "does not contain", ...).
- An answerable question **only passes if the expected value also appears in one of the
  run's own tool results** (SQL rows or a calculator result). A correct-looking number the
  model typed itself, or got close to by luck, fails as `answer_not_from_tools`. Example:
  one run used list prices instead of paid prices, then did the division in prose and
  wrote 42.11%, which is within 0.01 of the true 42.10%. The looser first checker passed it.
- `python -m sqlagent.evaluate --rescore runs/<tag>` re-judges a saved log with the current
  checker, without calling the model, so old and new runs are scored the same way.
- pass@1 is the mean success over all runs. pass^3 is the share of questions that passed in
  all 3 trials.
- Failure labels: `wrong_result`, `answer_not_from_tools`, `sql_error_unrecovered`,
  `no_tool_guess`, `false_refusal`, `made_up_answer`, `step_cap`, `budget_cap`, `error`.

Known checker limitations (spot-check the `runs.jsonl` records behind your headline numbers):
- The number check is lenient. An answer that lists many numbers can match by accident.
- The unanswerable check only tests *that* the agent declined, not *why*. For example, one
  smoke run refused q17 ("phone number of customer 5") with the wrong reason ("customer_id
  is missing") and still passed.

## Results

All five runs: 19 questions × 3 trials, temperature 0.3, all re-scored with the current
checker (`--rescore`). Every run ended with status `ok` except one `step_cap` in guards-v1.

| Run (`runs/…`) | pass@1 | pass^3 | simple | join | grouping | calc | multi-tool | unanswerable | tokens/run | s/run |
|---|---|---|---|---|---|---|---|---|---|---|
| `qwen2.5-3b_inline_no-guards` (v0) | 0.614 | 0.526 | 12/12 | 8/9 | 6/12 | 5/9 | 0/6 | 4/9 | 1972 | 15.4 |
| `qwen2.5-3b_inline_guards-v1` | 0.649 | 0.526 | 12/12 | 9/9 | 6/12 | 4/9 | 3/6 | 3/9 | 3412 | 23.4 |
| `qwen2.5-3b_inline` (current) | 0.684 | 0.579 | 12/12 | 9/9 | 8/12 | 5/9 | 1/6 | 4/9 | 2848 | 23.8 |
| `qwen2.5-3b_tool` | 0.684 | 0.526 | 12/12 | 6/9 | 9/12 | 2/9 | 2/6 | 8/9 | 3757 | 29.9 |
| `qwen2.5-latest_inline` (7B) | 0.789 | 0.737 | 12/12 | 9/9 | 12/12 | 4/9 | 5/6 | 3/9 | 2441 | 27.1 |

What the runs show (from reading the failed traces, not just the counts):

- **Guards on 3B:** pass@1 rose from 0.614 to 0.684 and pass^3 from 0.526 to 0.579. The gains
  are in join/grouping, where the agent now fixes its SQL instead of refusing after an error
  (the schema-aware hints), plus q15 trial 1, which a refusal push-back turned into a
  correct answer. The guards were tuned on these same questions, so treat the gain as an
  optimistic estimate. Remaining 3B failures are mostly
  the model's own: wrong SQL logic (q12/q13 average per *item* instead of per *order*),
  inventing profit/returns from revenue/cancellations (q18/q19), and q10, where the model saw
  the "join products.name" hint, said "I would need to join…", and then stopped instead of doing it.
- **Inline vs tool SQL generation (3B):** same pass@1 (0.684), lower pass^3 (0.526), about 32% more
  tokens and 26% more time per run in tool mode. The agent can't see the columns, so it can't
  catch mistakes made by the separate SQL writer. That writer wrote `WHERE status='cancelled'` for both the count and the total (q14:
  100%), and it filtered to completed orders for customer revenue (q05). Tool mode refuses
  unanswerable questions far better (8/9 vs 4/9), because the agent never sees the
  columns it could misuse as stand-ins. **Inline is the default:** it is cheaper and more
  consistent, and it is better on calculation.
- **7B vs 3B (bonus):** 0.789 / 0.737 vs 0.684 / 0.579, with fewer tokens per run (fewer retries)
  and no duplicate calls. 7B is perfect on grouping and nearly so on multi-tool, but it is
  *worse* on unanswerable questions (3/9). It confidently reports "profit" as revenue minus
  list price and "returns" as cancelled items. Both models share the q12 per-item averaging mistake.

## Model tag

`qwen2.5:3b` (Ollama id `357c53fb659c`). For the bonus comparison, use `qwen2.5:latest` (7B).
Model files are not committed.
