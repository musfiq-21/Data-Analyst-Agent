# Eval summary: qwen2.5:3b (inline mode)

- pass@1: **0.649**
- pass^3: **0.526**
- runs: 57 (19 questions x 3), temperature 0.3, nudge True, guards True
- statuses: {'ok': 56, 'step_cap': 1}
- guard push-backs: {'refusal_after_error': 7, 'ungrounded_numbers': 10}, runs with duplicate calls: 8
- avg tokens/run: 3412.4, avg seconds/run: 23.37

| id | category | passes |
|---|---|---|
| q01 | simple | 3/3 |
| q02 | simple | 3/3 |
| q03 | simple | 3/3 |
| q04 | simple | 3/3 |
| q05 | join | 3/3 |
| q06 | join | 3/3 |
| q07 | join | 3/3 |
| q08 | grouping | 3/3 |
| q09 | grouping | 3/3 |
| q10 | grouping | 0/3 |
| q11 | grouping | 0/3 |
| q12 | calculation | 2/3 |
| q13 | calculation | 0/3 |
| q14 | calculation | 2/3 |
| q15 | multi_tool | 1/3 |
| q16 | multi_tool | 2/3 |
| q17 | unanswerable | 3/3 |
| q18 | unanswerable | 0/3 |
| q19 | unanswerable | 0/3 |

## Failures

| type | count |
|---|---|
| wrong_result | 7 |
| made_up_answer | 6 |
| false_refusal | 4 |
| step_cap | 1 |
| sql_error_unrecovered | 1 |
| answer_not_from_tools | 1 |
