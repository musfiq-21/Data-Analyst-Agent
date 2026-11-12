# Eval summary: qwen2.5:3b (inline mode)

- pass@1: **0.614**
- pass^3: **0.526**
- runs: 57 (19 questions x 3), temperature 0.3, nudge True, guards False
- statuses: {'ok': 57}
- guard push-backs: {}, runs with duplicate calls: 0
- avg tokens/run: 1971.7, avg seconds/run: 15.4

| id | category | passes |
|---|---|---|
| q01 | simple | 3/3 |
| q02 | simple | 3/3 |
| q03 | simple | 3/3 |
| q04 | simple | 3/3 |
| q05 | join | 2/3 |
| q06 | join | 3/3 |
| q07 | join | 3/3 |
| q08 | grouping | 3/3 |
| q09 | grouping | 3/3 |
| q10 | grouping | 0/3 |
| q11 | grouping | 0/3 |
| q12 | calculation | 3/3 |
| q13 | calculation | 0/3 |
| q14 | calculation | 2/3 |
| q15 | multi_tool | 0/3 |
| q16 | multi_tool | 0/3 |
| q17 | unanswerable | 3/3 |
| q18 | unanswerable | 1/3 |
| q19 | unanswerable | 0/3 |

## Failures

| type | count |
|---|---|
| false_refusal | 9 |
| wrong_result | 8 |
| made_up_answer | 5 |
