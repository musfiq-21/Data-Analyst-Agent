"""Run the question set N times, check answers, and report pass@1 / pass^k.

    python -m sqlagent.evaluate                       # qwen2.5:3b, 3 trials, inline mode
    python -m sqlagent.evaluate --model qwen2.5:latest
    python -m sqlagent.evaluate --sql-mode tool
    python -m sqlagent.evaluate --no-guards --tag qwen2.5-3b_inline_no-guards
    python -m sqlagent.evaluate --only q01,q07 --trials 1
    python -m sqlagent.evaluate --rescore runs/qwen2.5-3b_inline   # re-judge a saved log

Expected answers are computed here from each question's own `expected_sql`, run
directly against the database (not through the agent). They are also written to
data/expected_answers.json so they can be submitted and inspected.

A run passes when (1) its final answer states the expected value, and (2) that value
appears in one of its own tool results, i.e. the answer is based on what the tools
returned and not a guess that happens to be close. Unanswerable questions pass on a
refusal.

Outputs go to runs/<tag>/: runs.jsonl (one record per run), summary.json, summary.md.
"""
import argparse
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from sqlagent import DEFAULT_DB, DEFAULT_MODEL, PROJECT_ROOT, safe_console
from sqlagent.agent import is_refusal, numbers_in, numbers_in_value

QUESTIONS = os.path.join(PROJECT_ROOT, "data", "questions.json")
EXPECTED_OUT = os.path.join(PROJECT_ROOT, "data", "expected_answers.json")
# 0.1% relative or 0.01 absolute: covers rounding to 2 decimals (or to a whole number
# for large values) but not a near miss from a wrong method (42.15% vs 42.10%).
REL_TOL, ABS_TOL = 0.001, 0.01


# ---------------------------------------------------------------- checking

def _close(a, b):
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def check_answer(expected, answer):
    """True if the agent's free-text `answer` matches the expected spec."""
    if not answer:
        return False
    kind = expected["kind"]
    if kind == "unanswerable":
        return is_refusal(answer)
    if is_refusal(answer):
        return False
    if kind == "number":
        return any(_close(n, expected["value"]) for n in numbers_in(answer))
    if kind == "numbers":
        found = numbers_in(answer)
        return all(any(_close(n, v) for n in found) for v in expected["value"])
    if kind == "text":
        return str(expected["value"]).lower() in answer.lower()
    raise ValueError(f"unknown expected kind {kind!r}")


def supported_by_tools(expected, run):
    """True if the expected value appears in at least one of the run's tool results."""
    kind = expected["kind"]
    if kind == "unanswerable":
        return True
    results = [c.get("result") for c in run.get("tool_calls", [])]
    if kind == "text":
        blob = json.dumps(results, ensure_ascii=False, default=str).lower()
        return str(expected["value"]).lower() in blob
    sources = numbers_in_value(results)
    values = expected["value"] if kind == "numbers" else [expected["value"]]
    return all(any(_close(s, v) for s in sources) for v in values)


def judge(expected, run):
    """(passed, failure_label) for one run record."""
    stated = check_answer(expected, run.get("final_answer"))
    if stated and supported_by_tools(expected, run):
        return True, None
    if stated:
        return False, "answer_not_from_tools"
    return False, classify_failure(run, expected["kind"])


def pass_at_1(results):
    """Mean success over every individual run (questions weighted equally)."""
    per_q = [sum(r) / len(r) for r in results.values() if r]
    return sum(per_q) / len(per_q) if per_q else 0.0


def pass_hat_k(results):
    """Share of questions where *every* trial passed."""
    vals = [all(r) for r in results.values() if r]
    return sum(vals) / len(vals) if vals else 0.0


def classify_failure(run, expected_kind):
    """Label why a failed run failed (used for the failure analysis)."""
    if run.get("status") in ("step_cap", "budget_cap", "error"):
        return run["status"]
    answer = run.get("final_answer") or ""
    if expected_kind == "unanswerable":
        return "made_up_answer"
    if is_refusal(answer):
        return "false_refusal"
    if not run.get("used_tools"):
        return "no_tool_guess"
    sql_calls = [c for c in run.get("tool_calls", []) if c.get("name") == "run_sql"]
    if sql_calls and not sql_calls[-1]["result"].get("ok"):
        return "sql_error_unrecovered"
    return "wrong_result"


# ---------------------------------------------------------------- expected answers

def compute_expected(questions, db_path):
    """Run each question's expected_sql directly (independent of the agent)."""
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        out = {}
        for q in questions:
            kind = q["kind"]
            if kind == "unanswerable":
                out[q["id"]] = {"kind": kind, "value": None}
                continue
            row = conn.execute(q["expected_sql"]).fetchone()
            if kind == "number":
                value = round(float(row[0]), 4)
            elif kind == "numbers":
                value = [round(float(v), 4) for v in row]
            else:
                value = str(row[0])
            out[q["id"]] = {"kind": kind, "value": value}
        return out
    finally:
        conn.close()


def load_questions(path=QUESTIONS):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- summary

def summarize(records, meta, questions):
    """Build summary dict from judged run records."""
    results = defaultdict(list)
    failures, statuses, pushbacks = Counter(), Counter(), Counter()
    examples = {}
    total_tokens = total_seconds = dup_runs = 0
    for r in records:
        results[r["id"]].append(r["passed"])
        statuses[r["status"]] += 1
        total_tokens += r["total_tokens"]
        total_seconds += r["seconds"]
        pushbacks.update(r.get("guard_pushbacks", []))
        dup_runs += 1 if r.get("duplicate_calls") else 0
        if r["failure"]:
            failures[r["failure"]] += 1
            examples.setdefault(r["failure"], {"id": r["id"], "trial": r["trial"],
                                               "question": r["question"],
                                               "expected": r["expected"],
                                               "answer": r["final_answer"], "sql": r["sql"]})
    k = meta["trials"]
    n = len(records)
    order = [q["id"] for q in questions if q["id"] in results]
    return {
        **meta,
        "questions": len(results), "runs": n,
        "pass@1": round(pass_at_1(results), 4),
        f"pass^{k}": round(pass_hat_k(results), 4),
        "statuses": dict(statuses), "failures": dict(failures.most_common()),
        "guard_pushbacks": dict(pushbacks), "runs_with_duplicate_calls": dup_runs,
        "avg_tokens_per_run": round(total_tokens / max(n, 1), 1),
        "avg_seconds_per_run": round(total_seconds / max(n, 1), 2),
        "failure_examples": examples,
        "per_question": {qid: {"passes": sum(results[qid]), "trials": len(results[qid])}
                         for qid in order},
    }


def write_outputs(out_dir, records, summary, questions):
    with open(os.path.join(out_dir, "runs.jsonl"), "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)
    with open(os.path.join(out_dir, "summary.md"), "w", encoding="utf-8") as f:
        f.write(render_markdown(summary, questions))


def print_summary(s, log_path):
    k = s["trials"]
    print()
    print(f"pass@1 = {s['pass@1']:.3f}   pass^{k} = {s[f'pass^{k}']:.3f}   "
          f"over {s['questions']} questions x {k}")
    print(f"statuses: {s['statuses']}   failures: {s['failures']}")
    print(f"log: {log_path}")


def render_markdown(s, questions):
    k = s["trials"]
    lines = [f"# Eval summary: {s['model']} ({s['sql_mode']} mode)", "",
             f"- pass@1: **{s['pass@1']:.3f}**",
             f"- pass^{k}: **{s[f'pass^{k}']:.3f}**",
             f"- runs: {s['runs']} ({s['questions']} questions x {k}), "
             f"temperature {s['temperature']}, nudge {s['nudge']}, guards {s['guards']}",
             f"- statuses: {s['statuses']}",
             f"- guard push-backs: {s['guard_pushbacks']}, "
             f"runs with duplicate calls: {s['runs_with_duplicate_calls']}",
             f"- avg tokens/run: {s['avg_tokens_per_run']}, "
             f"avg seconds/run: {s['avg_seconds_per_run']}", "",
             "| id | category | passes |", "|---|---|---|"]
    cats = {q["id"]: q["category"] for q in questions}
    for qid, r in s["per_question"].items():
        lines.append(f"| {qid} | {cats.get(qid, '')} | {r['passes']}/{r['trials']} |")
    lines += ["", "## Failures", "", "| type | count |", "|---|---|"]
    for name, n in s["failures"].items():
        lines.append(f"| {name} | {n} |")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- entry points

def rescore(run_dir, db_path):
    """Re-judge a saved runs.jsonl with the current checker (no model calls)."""
    questions = load_questions()
    expected = compute_expected(questions, db_path)
    log_path = os.path.join(run_dir, "runs.jsonl")
    with open(log_path, encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]
    meta_path = os.path.join(run_dir, "summary.json")
    old = {}
    if os.path.exists(meta_path):
        with open(meta_path, encoding="utf-8") as f:
            old = json.load(f)
    for r in records:
        r.setdefault("guard_pushbacks", [])
        r.setdefault("duplicate_calls", 0)
        exp = expected[r["id"]]
        r["expected"], r["expected_kind"] = exp["value"], exp["kind"]
        r["passed"], r["failure"] = judge(exp, r)
    trials = max(r["trial"] for r in records)
    meta = {"model": records[0]["model"], "sql_mode": records[0]["sql_mode"],
            "trials": trials, "temperature": old.get("temperature", 0.3),
            "max_steps": old.get("max_steps", 8), "token_budget": old.get("token_budget", 20000),
            "nudge": old.get("nudge", True), "guards": old.get("guards", False),
            "rescored": True}
    summary = summarize(records, meta, questions)
    write_outputs(run_dir, records, summary, questions)
    print_summary(summary, log_path)


def main(argv=None):
    from sqlagent.agent import Agent
    from sqlagent.llm import OllamaChat

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--trials", type=int, default=3)
    ap.add_argument("--sql-mode", choices=["inline", "tool"], default="inline")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--max-steps", type=int, default=8)
    ap.add_argument("--token-budget", type=int, default=20000)
    ap.add_argument("--no-nudge", action="store_true",
                    help="disable the 'use a tool first' reminder (for before/after comparison)")
    ap.add_argument("--no-guards", action="store_true",
                    help="disable the orchestration guards = the first version of the loop")
    ap.add_argument("--only", help="comma-separated question ids")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--tag", help="output folder name under runs/ (default: model + mode)")
    ap.add_argument("--rescore", metavar="RUN_DIR",
                    help="re-judge an existing runs/<tag> folder without calling the model")
    args = ap.parse_args(argv)
    safe_console()

    if not os.path.exists(args.db):
        sys.exit(f"database not found at {args.db}. Run: python -m sqlagent.build_db")
    if args.rescore:
        return rescore(args.rescore, args.db)

    questions = load_questions()
    expected = compute_expected(questions, args.db)
    with open(EXPECTED_OUT, "w", encoding="utf-8") as f:
        json.dump({q["id"]: {"question": q["question"], **expected[q["id"]]}
                   for q in questions}, f, indent=2, ensure_ascii=False)
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    tag = args.tag or f"{args.model.replace(':', '-')}_{args.sql_mode}"
    out_dir = os.path.join(PROJECT_ROOT, "runs", tag)
    os.makedirs(out_dir, exist_ok=True)
    log_path = os.path.join(out_dir, "runs.jsonl")

    llm = OllamaChat(args.model, temperature=args.temperature)
    agent = Agent(llm, args.db, sql_mode=args.sql_mode, max_steps=args.max_steps,
                  token_budget=args.token_budget, nudge=not args.no_nudge,
                  guards=not args.no_guards)

    records = []
    t_start = time.perf_counter()
    with open(log_path, "w", encoding="utf-8") as log:  # live log, rewritten at the end
        for q in questions:
            exp = expected[q["id"]]
            for trial in range(1, args.trials + 1):
                run = agent.run(q["question"])
                passed, failure = judge(exp, run)
                run.update({"id": q["id"], "category": q["category"], "trial": trial,
                            "expected": exp["value"], "expected_kind": exp["kind"],
                            "passed": passed, "failure": failure})
                records.append(run)
                log.write(json.dumps(run, ensure_ascii=False, default=str) + "\n")
                log.flush()
                mark = "PASS" if passed else f"FAIL ({failure})"
                ans = (run["final_answer"] or "").replace("\n", " ")[:90]
                print(f"{q['id']} t{trial} {run['status']:10s} {mark:30s} "
                      f"{run['total_tokens']:6d} tok {run['seconds']:6.1f}s | {ans}")

    meta = {"model": args.model, "sql_mode": args.sql_mode, "trials": args.trials,
            "temperature": args.temperature, "max_steps": args.max_steps,
            "token_budget": args.token_budget, "nudge": not args.no_nudge,
            "guards": not args.no_guards,
            "wall_seconds": round(time.perf_counter() - t_start, 1)}
    summary = summarize(records, meta, questions)
    write_outputs(out_dir, records, summary, questions)
    print_summary(summary, log_path)


if __name__ == "__main__":
    main()
