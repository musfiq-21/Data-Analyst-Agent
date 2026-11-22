"""The agent loop, written by hand (no agent framework).

Two ways to produce SQL (the assignment's design choice), selected with sql_mode:

  inline (default): the full schema is in the agent's system prompt and the agent
      writes SQL itself as the `query` argument of run_sql. One model call per step.
  tool: the agent only sees table names. It calls generate_sql(question), which makes
      a separate, focused model call that sees the full schema and returns one query.

Either way the numbers come from run_sql / calculator results, never from the model.
"""
import json
import re
import time

from sqlagent.llm import notional_cost
from sqlagent.tools import (DEFAULT_ROW_LIMIT, calculator, column_index, describe_table,
                            get_schema, run_sql, to_text)

DEFINITIONS = """\
Definitions:
- Revenue = SUM(order_items.quantity * order_items.unit_price). Use the unit_price in
  order_items (the price actually paid), not products.unit_price.
- Order value = the revenue of one order. "Average order value" = average over orders.
- Count every order (including 'cancelled') unless the question says otherwise.
- Dates are TEXT 'YYYY-MM-DD'; use strftime('%Y', order_date) = '2025' for a year."""

RULES = """\
Rules:
1. Never guess numbers and never do arithmetic in your head. Every number in your
   answer must come from a tool result: run_sql for data (let SQL do SUM, AVG, COUNT,
   MIN, MAX), calculator for arithmetic on numbers you already got.
2. Use only the tables and columns that exist. If the question needs data the database
   does not have, reply with "CANNOT ANSWER:" followed by what is missing. Do not invent
   a number.
3. If a tool returns an error, read it, fix your call, and try again.
4. When you have the result, give a short final answer that states the number(s)
   (rounded to 2 decimals) or the name asked for. Do not call more tools after that."""

# Added when guards are on (generic, not tied to any test question).
GUARD_RULES = """
5. A tool error or an empty/NULL result means your query is wrong. It does NOT mean the
   data is missing. Read the hint, fix the query and run it again.
6. Answer exactly what was asked. If the asked-for quantity is not recorded by any column,
   reply CANNOT ANSWER. Do not approximate it with a different column or status.
7. For "which" / "who" questions, answer with the name, not the ID (join the table that
   holds the name)."""

INLINE_SYSTEM = """You are a data analyst agent. You answer questions about a shop's \
SQLite database by calling tools.

{rules}

{definitions}

Database schema:
{schema}"""

TOOL_SYSTEM = """You are a data analyst agent. You answer questions about a shop's \
SQLite database by calling tools.

{rules}

The database has these tables: {tables}.
To get data: call generate_sql with a precise description of the data you need, then \
pass the SQL it returns to run_sql. Use describe_table if you need column names.

{definitions}"""

SQL_WRITER_SYSTEM = """You write exactly one SQLite SELECT query that answers the \
request. Use only the tables and columns below. Return only the SQL, no explanation.
If the request needs data that does not exist in the schema, return exactly: \
SELECT 'CANNOT ANSWER' AS error

{definitions}

Schema:
{schema}"""


def _fn(name, description, props, required):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": props, "required": required}}}


TOOL_RUN_SQL = _fn("run_sql", "Run ONE read-only SQLite SELECT query and return the rows "
                   "(or an error message).",
                   {"query": {"type": "string", "description": "A single SELECT statement"}},
                   ["query"])
TOOL_CALC = _fn("calculator", "Evaluate an arithmetic expression, e.g. "
                "'(1250 - 1000) / 1000 * 100'. Supports + - * / ** ( ) round abs min max.",
                {"expression": {"type": "string"}}, ["expression"])
TOOL_DESCRIBE = _fn("describe_table", "Look up the schema. With no table, lists tables. "
                    "With a table name, returns its columns.",
                    {"table": {"type": "string", "description": "optional table name"}}, [])
TOOL_GENERATE = _fn("generate_sql", "Write a SQLite SELECT query for a data request. "
                    "Returns the SQL text; run it with run_sql.",
                    {"question": {"type": "string",
                                  "description": "What data the query must return"}},
                    ["question"])

NUDGE = ("You answered without calling any tool. Numbers must come from tools: call "
         "run_sql (and calculator if needed) first, then give the final answer. If the "
         "database cannot answer, reply with CANNOT ANSWER: and the reason.")

REFUSAL_AFTER_ERROR = (
    "Your last query failed or returned no data. That does not mean the data is missing; "
    "it means the SQL was wrong. Read the error and hint, check the column names in the "
    "schema, fix the query and call run_sql again.")

UNGROUNDED = ("These numbers in your answer do not appear in any tool result: {nums}. "
              "Every number must come from a tool. Compute it with the calculator (or SQL), "
              "then give the final answer using the tool's result.")

DUPLICATE_NOTE = ("You already made this exact call at step {step}. Its result is repeated "
                  "below. Do not call it again: use it, or try a different query.")

EMPTY_RETRY = ("Your last reply was empty (the tool call could not be read). Try again: "
               "call ONE tool with valid JSON arguments, or give the final answer as text.")

REFUSAL_RE = re.compile(r"cannot answer|can't answer|can not answer|unable to answer|"
                        r"not (?:available|possible|present|stored|recorded|included|in the database)|"
                        r"no (?:such )?(?:data|information|column|table|record)s?\b|"
                        r"does(?: not|n't) (?:contain|have|store|include|record|track)|"
                        r"do(?: not|n't) (?:have|contain|store|track)",
                        re.IGNORECASE)


def is_refusal(text):
    return bool(text) and bool(REFUSAL_RE.search(text))


_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?|-?\.\d+")


def numbers_in(text):
    """All numbers written in `text` ("1,192.50" -> 1192.5)."""
    out = []
    for tok in _NUM_RE.findall(text or ""):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def numbers_in_value(value):
    """All numbers inside a tool result (nested dicts/lists); strings are not scanned."""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, (int, float)):
        return [float(value)]
    if isinstance(value, dict):
        return [n for v in value.values() for n in numbers_in_value(v)]
    if isinstance(value, (list, tuple)):
        return [n for v in value for n in numbers_in_value(v)]
    return []


def _grounded(n, sources):
    n = abs(n)
    for s in sources:
        s = abs(s)
        if abs(n - s) <= max(0.006, 0.005 * s):  # same value, maybe rounded
            return True
        if n.is_integer() and abs(n - s) < 0.5:  # rounded to a whole number
            return True
    return False


def ungrounded_numbers(answer, sources):
    """Numbers in `answer` that match no source number. Small integers (0-10, list
    markers, 'top 3') and 100 (percent) are allowed without a source."""
    bad = []
    for n in numbers_in(answer):
        if n.is_integer() and (abs(n) <= 10 or abs(n) == 100):
            continue
        if not _grounded(n, sources) and n not in bad:
            bad.append(n)
    return bad


def extract_sql(text):
    """Pull a bare SQL statement out of a model reply (strips ``` fences and chatter)."""
    m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if m:
        text = m.group(1)
    m = re.search(r"\b(WITH|SELECT)\b", text, re.IGNORECASE)
    if m:
        text = text[m.start():]
    text = text.strip()
    if ";" in text:
        text = text[:text.index(";")]
    return text.strip()


def parse_text_tool_call(content):
    """Some small models write the tool call as JSON text instead of using the tools
    API. Accept {"name": ..., "arguments": {...}} or {"tool": ..., "args": {...}}."""
    if not content or "{" not in content:
        return []
    candidate = content
    m = re.search(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL) or \
        re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if m:
        candidate = m.group(1)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        obj = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(obj, dict):
        return []
    name = obj.get("name") or obj.get("tool")
    args = obj.get("arguments", obj.get("args", obj.get("parameters", {})))
    if isinstance(name, str) and isinstance(args, dict):
        return [{"name": name, "arguments": args}]
    return []


class Agent:
    def __init__(self, llm, db_path, sql_mode="inline", max_steps=8, token_budget=20000,
                 row_limit=DEFAULT_ROW_LIMIT, nudge=True, guards=True, max_pushbacks=2,
                 cost_cap_usd=None, max_retries=3):
        """guards=True turns on the orchestration guards (error hints, refusal-after-error
        push-back, grounding check, duplicate-call cache, extra prompt rules).
        guards=False reproduces the first version of the loop, for before/after numbers.
        cost_cap_usd: stop the run once its notional cost (slide 20 rates) reaches this.
        max_retries: how many failed model calls / unusable replies a run may retry."""
        if sql_mode not in ("inline", "tool"):
            raise ValueError("sql_mode must be 'inline' or 'tool'")
        self.llm = llm
        self.db_path = db_path
        self.sql_mode = sql_mode
        self.max_steps = max_steps
        self.token_budget = token_budget
        self.row_limit = row_limit
        self.nudge = nudge
        self.guards = guards
        self.max_pushbacks = max_pushbacks
        self.cost_cap_usd = cost_cap_usd
        self.max_retries = max_retries
        self.schema = get_schema(db_path)
        self.columns = column_index(db_path)
        tables = describe_table(db_path)["tables"]
        self.table_names = tables
        rules = RULES + (GUARD_RULES if guards else "")
        if sql_mode == "inline":
            self.system = INLINE_SYSTEM.format(rules=rules, definitions=DEFINITIONS,
                                               schema=self.schema)
            self.tools = [TOOL_RUN_SQL, TOOL_CALC, TOOL_DESCRIBE]
        else:
            self.system = TOOL_SYSTEM.format(rules=rules, definitions=DEFINITIONS,
                                             tables=", ".join(tables))
            self.tools = [TOOL_GENERATE, TOOL_RUN_SQL, TOOL_CALC, TOOL_DESCRIBE]
        self.tool_names = {t["function"]["name"] for t in self.tools}

    # ------------------------------------------------------------ tools

    def _generate_sql(self, question, record):
        messages = [{"role": "system",
                     "content": SQL_WRITER_SYSTEM.format(definitions=DEFINITIONS,
                                                         schema=self.schema)},
                    {"role": "user", "content": question}]
        res = self.llm.chat(messages)
        record["prompt_tokens"] += res.prompt_tokens
        record["completion_tokens"] += res.completion_tokens
        sql = extract_sql(res.content)
        if not sql:
            return {"ok": False, "error": "SQL writer returned no query", "raw": res.content}
        return {"ok": True, "sql": sql}

    def dispatch(self, call, record):
        name, args = call.get("name"), call.get("arguments") or {}
        if name not in self.tool_names:
            return {"ok": False, "error": f"unknown tool {name!r}. "
                                          f"Available tools: {sorted(self.tool_names)}"}
        try:
            if name == "run_sql":
                query = args.get("query") or args.get("sql")
                if not query:
                    return {"ok": False, "error": "run_sql needs a 'query' argument"}
                record["sql"].append(query)
                result = run_sql(self.db_path, query, self.row_limit)
                return self._annotate(result) if self.guards else result
            if name == "calculator":
                return calculator(args.get("expression", ""))
            if name == "describe_table":
                return describe_table(self.db_path, args.get("table") or None)
            if name == "generate_sql":
                return self._generate_sql(args.get("question") or "", record)
        except Exception as e:  # a tool must never crash the loop
            return {"ok": False, "error": f"{name} failed: {type(e).__name__}: {e}"}
        return {"ok": False, "error": f"tool {name!r} not implemented"}

    # ------------------------------------------------------------ guards

    def _annotate(self, result):
        """Add a `hint` to run_sql results that small models typically misread."""
        if not result.get("ok"):
            err = result.get("error", "")
            hint = "This is a mistake in your SQL, not missing data. Fix the query and " \
                   "call run_sql again."
            m = re.search(r"no such column: ([\w.]+)", err)
            if m:
                prefix, _, col = m.group(1).rpartition(".")
                owners = self.columns.get(col.lower())
                if owners and prefix in owners:
                    # the column is right, but its table is not joined where it is used
                    hint = (f"Table '{prefix}' does have column '{col}', but '{prefix}' is not "
                            f"in the FROM clause of the (sub)query where you wrote "
                            f"{prefix}.{col}. Add it there with a JOIN on its key "
                            "(e.g. order_items.order_id = orders.id), or move the column "
                            "into the subquery that has that table. This is a mistake in "
                            "your SQL, not missing data.")
                elif owners:
                    hint = (f"Column '{col}' exists in table(s) {', '.join(owners)}. Join that "
                            f"table on its key, or qualify the column (table.{col}). If you "
                            "use a subquery, select the column inside it to use it outside. "
                            "This is a mistake in your SQL, not missing data.")
                else:
                    result["missing_everywhere"] = True
                    hint = (f"No table has a column named '{col}'. Check the schema: if what "
                            "the question asks for is not recorded anywhere, the answer is "
                            "CANNOT ANSWER; otherwise use the right column.")
            m = re.search(r"no such table: ([\w.]+)", err)
            if m:
                hint = (f"There is no table '{m.group(1)}'. Tables are: "
                        f"{', '.join(self.table_names)}.")
            result["hint"] = hint
            return result
        rows = result.get("rows") or []
        if not rows or all(v is None for row in rows for v in row):
            result["hint"] = ("The query returned no data (no rows or only NULL). Your WHERE "
                              "filters or JOINs matched nothing: check the values and the "
                              "join conditions, then run it again.")
        elif self._ids_without_names(result):
            result["hint"] = ("These rows contain IDs but no names. If the question asks "
                              "which / who, JOIN the table that holds the name (products.name "
                              "via product_id, customers.name via customer_id) and answer "
                              "with the name.")
        elif len(rows) > 1 and len(result.get("columns", [])) == 1 and \
                all(isinstance(r[0], (int, float)) for r in rows):
            result["hint"] = (f"This returned {len(rows)} rows of one number each. If the "
                              "question asks for a single total, remove the GROUP BY (or "
                              "add SUM) so the query returns one value.")
        return result

    @staticmethod
    def _ids_without_names(result):
        cols = [c.lower() for c in result.get("columns", [])]
        has_id = any(c == "id" or c.endswith("_id") for c in cols)
        has_text = any(isinstance(v, str) for row in result.get("rows", []) for v in row)
        return has_id and not has_text

    def _check_final(self, answer, record, question):
        """Return a push-back message if the final answer should not be accepted yet."""
        if not self.guards or len(record["guard_pushbacks"]) >= self.max_pushbacks:
            return None
        sql_results = [c["result"] for c in record["tool_calls"] if c["name"] == "run_sql"]
        if is_refusal(answer) and sql_results:
            last = sql_results[-1]
            failed = not last.get("ok") and not last.get("missing_everywhere")
            empty = last.get("ok") and "no data" in last.get("hint", "")
            if failed or empty:
                record["guard_pushbacks"].append("refusal_after_error")
                return REFUSAL_AFTER_ERROR
        if not is_refusal(answer):
            sources = numbers_in(question)
            for c in record["tool_calls"]:
                sources += numbers_in_value(c["result"])
            bad = ungrounded_numbers(answer, sources)
            if bad:
                record["guard_pushbacks"].append("ungrounded_numbers")
                return UNGROUNDED.format(nums=", ".join(f"{n:g}" for n in bad))
        return None

    # ------------------------------------------------------------ loop

    def run(self, question):
        t0 = time.perf_counter()
        record = {"question": question, "model": getattr(self.llm, "model", "?"),
                  "sql_mode": self.sql_mode, "status": None, "final_answer": None,
                  "error": None, "steps": 0, "tool_calls": [], "sql": [],
                  "used_tools": False, "nudged": False, "empty_replies": 0,
                  "guard_pushbacks": [], "duplicate_calls": 0,
                  "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                  "cost_usd": 0.0, "retries": 0, "seconds": 0.0}
        messages = [{"role": "system", "content": self.system},
                    {"role": "user", "content": question}]

        def used():
            return record["prompt_tokens"] + record["completion_tokens"]

        def cost():
            return notional_cost(record["prompt_tokens"], record["completion_tokens"])

        def retry():
            """Use one retry; False once the run has used them all."""
            if record["retries"] >= self.max_retries:
                return False
            record["retries"] += 1
            return True

        done_calls = {}  # (name, args json) -> (step, result), for the duplicate cache

        for step in range(1, self.max_steps + 1):
            if used() >= self.token_budget:
                record["status"] = "budget_cap"
                break
            if self.cost_cap_usd is not None and cost() >= self.cost_cap_usd:
                record["status"] = "cost_cap"
                break
            try:
                res = self.llm.chat(messages, tools=self.tools)
            except Exception as e:
                record["error"] = f"{type(e).__name__}: {e}"
                if retry():
                    continue
                record["status"] = "error"
                break
            record["error"] = None  # an earlier failed attempt was retried successfully
            record["steps"] = step
            record["prompt_tokens"] += res.prompt_tokens
            record["completion_tokens"] += res.completion_tokens

            calls = res.tool_calls or parse_text_tool_call(res.content)
            if calls:
                messages.append({"role": "assistant", "content": res.content or "",
                                 "tool_calls": [{"function": {"name": c["name"],
                                                              "arguments": c["arguments"]}}
                                                for c in calls]})
                for c in calls:
                    key = (c["name"], json.dumps(c["arguments"], sort_keys=True, default=str))
                    if self.guards and key in done_calls:
                        prev_step, prev = done_calls[key]
                        record["duplicate_calls"] += 1
                        # repeat the earlier result at top level (keeps ok / hint /
                        # missing_everywhere visible to the guards) plus a note
                        result = {**prev, "note": DUPLICATE_NOTE.format(step=prev_step),
                                  "duplicate_of_step": prev_step}
                    else:
                        result = self.dispatch(c, record)
                        done_calls[key] = (step, result)
                    if c["name"] in self.tool_names:
                        record["used_tools"] = True
                    record["tool_calls"].append({"step": step, "name": c["name"],
                                                 "arguments": c["arguments"],
                                                 "result": result})
                    messages.append({"role": "tool", "tool_name": c["name"],
                                     "content": to_text(result)})
                continue

            answer = (res.content or "").strip()
            if not answer:
                # Ollama returns an empty message when the model wrote a tool call it
                # could not parse. Do not accept "" as an answer; ask again.
                record["empty_replies"] += 1
                if not retry():
                    record["status"] = "retry_cap"
                    break
                messages.append({"role": "user", "content": EMPTY_RETRY})
                continue
            if (self.nudge and not record["used_tools"] and not record["nudged"]
                    and re.search(r"\d", answer) and not is_refusal(answer)):
                record["nudged"] = True
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "user", "content": NUDGE})
                continue
            pushback = self._check_final(answer, record, question)
            if pushback:
                messages.append({"role": "assistant", "content": answer})
                messages.append({"role": "user", "content": pushback})
                continue
            record["final_answer"] = answer
            record["status"] = "ok"
            break
        else:
            record["status"] = "step_cap"

        record["total_tokens"] = used()
        record["cost_usd"] = cost()
        record["seconds"] = round(time.perf_counter() - t0, 3)
        return record
