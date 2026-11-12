# Eval summary: qwen2.5:latest (inline mode)

- pass@1: **0.789**
- pass^3: **0.737**
- runs: 57 (19 questions x 3), temperature 0.3, nudge True, guards True
- statuses: {'ok': 57}
- guard push-backs: {'ungrounded_numbers': 7}, runs with duplicate calls: 0
- avg tokens/run: 2440.8, avg seconds/run: 27.05

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
| q10 | grouping | 3/3 |
| q11 | grouping | 3/3 |
| q12 | calculation | 0/3 |
| q13 | calculation | 1/3 |
| q14 | calculation | 3/3 |
| q15 | multi_tool | 3/3 |
| q16 | multi_tool | 2/3 |
| q17 | unanswerable | 3/3 |
| q18 | unanswerable | 0/3 |
| q19 | unanswerable | 0/3 |

## Failures

| type | count |
|---|---|
| made_up_answer | 6 |
| wrong_result | 4 |
| false_refusal | 2 |
