# SQL Calculation Agent: report

Agentic Software Engineering, Part 1, Assignment 1.
Model: **`qwen2.5:3b`** (Ollama id `357c53fb659c`), Ollama 0.40.16. Bonus comparison:
`qwen2.5:latest` (7B, id `845dbda0ea48`). All runs were local; no hosted API was used.

## 1. Architecture

```
question ─► agent loop (written by hand; max 8 steps, 20,000-token budget)
              │ system prompt: rules + definitions + full CREATE TABLE schema
              ▼
          qwen2.5:3b via Ollama /api/chat with native `tools`
              │ tool call                               │ text reply
              ▼                                         ▼
   run_sql ─► text guard ─► read-only SQLite      orchestration guards (below)
   calculator (AST, no eval)                      then final answer, status ok
   describe_table
   [generate_sql, tool mode only]
              │ result JSON (rows, or the error text plus a hint)
              └────────► appended as a `tool` message; the loop continues
```

- **Database.** `build_db.py` builds the shop database from seed 42 with four related
  tables: `customers` (50 rows), `products` (20), `orders` (200) and `order_items` (497).
- **Read-only safety, three layers.**
  1. A text guard accepts one statement only, which must start with `SELECT`/`WITH`. It
     rejects `DROP/DELETE/UPDATE/INSERT/ALTER/ATTACH/PRAGMA…` and multi-statement strings.
  2. The connection is opened with `mode=ro` and `PRAGMA query_only`.
  3. A SQLite authorizer denies every non-read action.

  Results are cut off at 50 rows. Each layer has its own tests in `tests/test_guard.py`.
- **Loop controls.** Every run ends with exactly one status: `ok`, `step_cap`, `budget_cap`
  or `error`. Token counts are Ollama's own `prompt_eval_count + eval_count`, summed per
  run.
- **Error handling.** SQL errors go back to the model as the tool result, so it can retry
  within the step cap.
- **SQL generation: design choice.** Both options are implemented (`--sql-mode`). I chose
  **inline** as the default: the schema is in the agent's prompt and the agent writes the
  SQL as the `run_sql` argument. The alternative, a `generate_sql` tool with its own model
  call, scored the same pass@1 (0.684), but it was less consistent (pass^3 0.526 vs 0.579)
  and cost 32% more tokens and 26% more time per run. In tool mode the agent never sees the
  columns, so it can't catch the writer's mistakes. For example, on q14 the writer filtered
  both the count and the total to `status='cancelled'` and reported 100% cancelled.

## 2. Evaluation

- **Questions.** There are 19, in 6 categories:
  - simple: 4
  - join: 3
  - grouping: 4
  - calculation: 3
  - multi-tool: 2
  - unanswerable: 3 (phone number, profit, returns: the schema has none of these)
- **Expected answers.** Each one comes from the question's own `expected_sql`, run directly
  on the database and not through the agent (`data/expected_answers.json`).
- **Pass rule.** A number must be within 0.1% (or 0.01) of the expected value. It must also
  **appear in one of the run's own tool results**, so a lucky or hand-computed number fails.
- **Trials.** 3 per question at temperature 0.3, so 57 runs per configuration. One record
  per run is in `runs/<tag>/runs.jsonl`: question, tool calls and results, SQL, answer,
  status, tokens and seconds.

| Configuration | pass@1 | pass^3 | pass rate per trial (t1 / t2 / t3) | tokens/run | s/run |
|---|---|---|---|---|---|
| **3B, inline, guards (submitted)** | **0.684** | **0.579** | 0.737 / 0.632 / 0.684 | 2848 | 23.8 |
| 3B, inline, no guards (before) | 0.614 | 0.526 | 0.632 / 0.579 / 0.632 | 1972 | 15.4 |
| 3B, tool mode, guards | 0.684 | 0.526 | 0.737 / 0.684 / 0.632 | 3757 | 29.9 |
| 7B, inline, guards (bonus) | 0.789 | 0.737 | 0.789 / 0.789 / 0.789 | 2441 | 27.1 |

All 57 runs of every configuration ended with status `ok`: none hit the step or token cap.

Per category for the submitted 3B run:

| simple | join | grouping | calculation | multi-tool | unanswerable |
|---|---|---|---|---|---|
| 12/12 | 9/9 | 8/12 | 5/9 | 1/6 | 4/9 |

The 3B model is reliable on single-table and simple join questions. It breaks down when a
question needs two levels of aggregation or a value the schema doesn't hold.

## 3. Failure analysis (submitted 3B run, 18 failed runs)

I read every failed trace instead of relying on the automatic labels alone.

| Failure type | Runs | Example |
|---|---|---|
| `wrong_result`: SQL runs but the logic is wrong | 7 | q12 "What is the average order value?" The model wrote `AVG(quantity * unit_price)` over `order_items`, which averages per *line item*: **208.44**. The question needs the sum per order first, then the average: 517.98. Both models make this mistake (7B: 3/3). |
| `false_refusal`: says it cannot answer an answerable question | 5 | q16 t1 "What percent of total revenue came from Electronics?" The model wrote `o.quantity` (the column is in `order_items`), got the error, and answered "CANNOT ANSWER: no such column: o.quantity". That happened even after two push-backs told it that an error is not missing data. |
| `made_up_answer`: answers a question the database cannot answer | 5 | q18 "What was our total profit in 2025?" There is no cost column. The model computed paid price minus list price and answered "**-1592.10**", with no caveat. q19 ("returns") was answered with a count of order items. |

One `sql_error_unrecovered` remains (q13 t2): the SQL kept failing and the capped
push-backs ran out.

**Honest notes on the labels.** All three q10 failures are labelled `false_refusal`, but the
cause is orchestration, not model knowledge. The model found product ID 15 and got a
hint to join `products.name`. It then wrote "I would need to join the products table… I
cannot proceed" and stopped, and the loop accepted that as the final answer. A push-back for
"announced a step but didn't take it" would probably fix this. I have not built it.

The unanswerable check only tests *that* the agent declined, not *why*.

## 4. One change and its effect: orchestration guards

**What I saw.** I read all 22 failed traces of the first version. About two-thirds were
loop problems, not model problems:
- The model read its own SQL error as "the data doesn't exist" and refused (9 `false_refusal`).
- It typed numbers it had worked out itself instead of using the calculator.
- It repeated the same query.
- It answered "which product" with an ID.

**What I changed.** I added guards to the loop (`agent.py`, tested in `tests/test_guards.py`):
- Schema-aware error hints. For example, `no such column: quantity` gets the hint "it exists
  in order_items; join it".
- One push-back when the model refuses right after a failed or empty query.
- A grounding check: every number in the answer must match a tool result or the question.
- A duplicate-call cache.
- Hints when the result is NULL, holds only IDs, or has several rows for a one-number question.

The push-backs are capped at 2 per run.

| | pass@1 | pass^3 | false refusals | tokens/run |
|---|---|---|---|---|
| Before (`runs/qwen2.5-3b_inline_no-guards`) | 0.614 | 0.526 | 9 | 1972 |
| After (`runs/qwen2.5-3b_inline`) | **0.684** | **0.579** | 5 | 2848 |

Both runs were re-scored with the same checker (`--rescore`).
- **The gain** comes from join and grouping questions: 14/21 passed before and 17/21 after.
  In those runs the model now fixes its SQL instead of refusing.
- **The cost:** 44% more tokens and 55% more time per run.
- **Caveat:** the guards are generic (no guard mentions a question), but I designed them
  after seeing failures on these same 19 questions. The gain is therefore optimistic. A
  held-out question set would give a fairer number.
- **Not improved:** unanswerable questions (4/9 before and after). No hint helps a model
  that believes "profit" can be computed.

## 5. Bonus: 3B vs 7B on the same questions

The 7B model scores higher (0.789 / 0.737). It does that with fewer tokens per run, because
it retries less and makes no duplicate calls. Its pass rate was identical in all 3 trials.
It is perfect on grouping (12/12) and nearly so on multi-tool questions (5/6).

It is **worse** on unanswerable questions (3/9 vs 4/9): it confidently invents "profit" and
"returns" in every trial. A bigger model writes better SQL, but it doesn't know better
when to say no.

## 6. Reproduce

The 5 commands in `README.md` rebuild the database, run the 89 offline tests and run the
evaluation. The `--model`, `--sql-mode` and `--no-guards` flags reproduce the other rows.

AI-assistant use is logged in `AI_LOG.md`.
