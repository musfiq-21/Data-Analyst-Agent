"""Ask the agent one question and print the trace.

    python -m sqlagent.ask "How many customers are there?"
    python -m sqlagent.ask --sql-mode tool --model qwen2.5:latest "Which city has the most customers?"
"""
import argparse
import json

from sqlagent import DEFAULT_DB, DEFAULT_MODEL, safe_console
from sqlagent.agent import Agent
from sqlagent.llm import OllamaChat


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--sql-mode", choices=["inline", "tool"], default="inline")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", action="store_true", help="print the full run record as JSON")
    args = ap.parse_args()
    safe_console()

    agent =Agent(OllamaChat(args.model), args.db, sql_mode=args.sql_mode)
    run = agent.run(args.question)
    if args.json:
        print(json.dumps(run, indent=2, ensure_ascii=False, default=str))
        return
    for c in run["tool_calls"]:
        res = json.dumps(c["result"], ensure_ascii=False, default=str)
        print(f"[step {c['step']}] {c['name']}({json.dumps(c['arguments'])})\n    -> {res[:300]}")
    print(f"\nstatus: {run['status']}   steps: {run['steps']}   tokens: {run['total_tokens']}"
          f"   time: {run['seconds']}s")
    print(f"answer: {run['final_answer'] or run['error']}")


if __name__ == "__main__":
    main()
