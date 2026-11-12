# Eval summary: qwen2.5:3b (tool mode)

- pass@1: **0.684**
- pass^3: **0.526**
- runs: 57 (19 questions x 3), temperature 0.3, nudge True, guards True
- statuses: {'ok': 57}
- guard push-backs: {'refusal_after_error': 7, 'ungrounded_numbers': 3}, runs with duplicate calls: 5
- avg tokens/run: 3757.1, avg seconds/run: 29.94

| id | category | passes |
|---|---|---|
| q01 | simple | 3/3 |
| q02 | simple | 3/3 |
| q03 | simple | 3/3 |
| q04 | simple | 3/3 |
| q05 | join | 0/3 |
| q06 | join | 3/3 |
| q07 | join | 3/3 |
| q08 | grouping | 1/3 |
| q09 | grouping | 2/3 |
| q10 | grouping | 3/3 |
| q11 | grouping | 3/3 |
| q12 | calculation | 1/3 |
| q13 | calculation | 0/3 |
| q14 | calculation | 1/3 |
| q15 | multi_tool | 2/3 |
| q16 | multi_tool | 0/3 |
| q17 | unanswerable | 3/3 |
| q18 | unanswerable | 2/3 |
| q19 | unanswerable | 3/3 |

## Failures

| type | count |
|---|---|
| wrong_result | 9 |
| false_refusal | 7 |
| sql_error_unrecovered | 1 |
| made_up_answer | 1 |
