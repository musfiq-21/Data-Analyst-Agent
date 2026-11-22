"""Tools the agent can call: a guarded read-only SQL executor, a calculator,
and a schema lookup. The SQL generator tool lives in agent.py because it needs
the model.

Safety layers for run_sql (each one alone would stop a write):
  1. check_sql: text guard - one statement, starts with SELECT/WITH, no write keywords.
  2. open_readonly: SQLite URI mode=ro plus PRAGMA query_only.
  3. an authorizer callback that denies every action except reads.
"""
import ast
import json
import math
import operator
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

DEFAULT_ROW_LIMIT = 50

# Scanned anywhere in the query. REPLACE is not listed: replace() is a read-only string
# function, and a REPLACE INTO statement is already stopped by the SELECT/WITH check.
FORBIDDEN = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
             "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "TRUNCATE", "GRANT",
             "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE"]
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(FORBIDDEN) + r")\b", re.IGNORECASE)
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def check_sql(sql):
    """Return None if `sql` is an allowed read-only query, else a reason string."""
    if not isinstance(sql, str):
        return "rejected: query must be a string"
    text = _COMMENT_RE.sub(" ", sql).strip()
    text = text.rstrip(";").strip()
    if not text:
        return "rejected: empty query"
    if ";" in text:
        return "rejected: only one statement is allowed (found ';' in the middle)"
    first = text.split(None, 1)[0].upper()
    if first not in ("SELECT", "WITH"):
        return f"rejected: only SELECT queries are allowed, got {first}"
    m = _FORBIDDEN_RE.search(text)
    if m:
        return f"rejected: keyword {m.group(1).upper()} is not allowed (read-only)"
    return None


_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}
if hasattr(sqlite3, "SQLITE_RECURSIVE"):
    _ALLOWED_ACTIONS.add(sqlite3.SQLITE_RECURSIVE)


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED_ACTIONS else sqlite3.SQLITE_DENY


def open_readonly(db_path):
    """Open the database read-only. Raises sqlite3.Error if the file is missing."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.execute("PRAGMA query_only = ON")
    conn.set_authorizer(_authorizer)
    return conn


def run_sql(db_path, query, row_limit=DEFAULT_ROW_LIMIT):
    """Run one SELECT. Never raises: returns {"ok": False, "error": ...} on any problem."""
    reason = check_sql(query)
    if reason:
        return {"ok": False, "error": reason}
    try:
        conn = open_readonly(db_path)
    except sqlite3.Error as e:
        return {"ok": False, "error": f"cannot open database: {e}"}
    try:
        cur = conn.execute(query)
        columns = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(row_limit + 1)
        truncated = len(rows) > row_limit
        rows = [list(r) for r in rows[:row_limit]]
        return {"ok": True, "columns": columns, "rows": rows,
                "row_count": len(rows), "truncated": truncated}
    except sqlite3.Error as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    finally:
        conn.close()


# ---------------------------------------------------------------- calculator

_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
           ast.Mod: operator.mod, ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCS = {"round": round, "abs": abs, "min": min, "max": max, "sqrt": math.sqrt}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and not node.keywords:
        return _FUNCS[node.func.id](*[_eval(a) for a in node.args])
    raise ValueError(f"unsupported expression element: {type(node).__name__}")


def calculator(expression):
    """Evaluate plain arithmetic: + - * / // % ** ( ) round abs min max sqrt."""
    try:
        if not isinstance(expression, str) or len(expression) > 500:
            raise ValueError("expression must be a string of at most 500 characters")
        result = _eval(ast.parse(expression, mode="eval"))
        if isinstance(result, float):
            result = round(result, 6)
        return {"ok": True, "expression": expression, "result": result}
    except ZeroDivisionError:
        return {"ok": False, "error": "division by zero"}
    except (ValueError, SyntaxError, TypeError) as e:
        return {"ok": False, "error": f"calculator error: {e}"}


# ---------------------------------------------------------------- schema

def get_schema(db_path):
    """CREATE TABLE statements (with comments) for every user table."""
    conn = open_readonly(db_path)
    try:
        rows = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                            "AND name NOT LIKE 'sqlite_%' ORDER BY rowid").fetchall()
    finally:
        conn.close()
    return "\n".join(r[0] + ";" for r in rows)


def column_index(db_path):
    """{column_name_lower: [tables that have it]} - used to turn 'no such column'
    errors into a useful hint. Reads the schema only, never user data."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        index = {}
        for t in tables:
            for col in conn.execute(f'PRAGMA table_info("{t}")'):
                index.setdefault(col[1].lower(), []).append(t)
        return index
    finally:
        conn.close()


def describe_table(db_path, table=None):
    """Schema lookup tool: one table's CREATE statement, or all table names."""
    conn = open_readonly(db_path)
    try:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        if not table:
            return {"ok": True, "tables": names}
        row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                           (table,)).fetchone()
        if not row:
            return {"ok": False, "error": f"no table named {table!r}; tables are {names}"}
        return {"ok": True, "table": table, "schema": row[0]}
    finally:
        conn.close()


def to_text(result):
    """Serialise a tool result for the model (compact JSON)."""
    return json.dumps(result, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- tool contract

@dataclass(frozen=True)
class Tool:
    """One tool: what the model sees (name, description, input_schema) and what the
    agent checks. Arguments are validated against input_schema BEFORE fn runs, and the
    result against output_schema AFTER, so a bad call or a broken tool never passes
    silently. fn takes the validated arguments dict and returns a result dict."""
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    fn: Optional[Callable] = None

    def spec(self):
        """The Ollama / OpenAI-style tool definition sent to the model."""
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": self.input_schema}}


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool,
          "array": list, "object": dict, "null": type(None)}


def _is_type(value, name):
    if isinstance(value, bool) and name in ("integer", "number"):
        return False  # True is an int in Python, not a number in JSON
    return isinstance(value, _TYPES[name])


def validate(value, schema, path="$"):
    """Check value against the JSON Schema subset the tools use: type (or a list of
    types), properties, required, items, enum, minLength, anyOf. Returns a list of
    readable errors (empty = valid). Standard library only, like the rest of the agent.
    shortcut: no $ref/pattern/format/numeric bounds; use the jsonschema package if a
    tool needs them."""
    types = schema.get("type")
    if types:
        types = [types] if isinstance(types, str) else types
        if not any(_is_type(value, t) for t in types):
            return [f"{path} must be {' or '.join(types)}, got {type(value).__name__}"]
    if "anyOf" in schema:
        options = [validate(value, sub, path) for sub in schema["anyOf"]]
        if all(options):
            return [f"{path} matches none of the allowed shapes: "
                    + " | ".join("; ".join(o) for o in options)]
    errors = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}, got {value!r}")
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        errors.append(f"{path} must not be empty")
    if isinstance(value, dict):
        errors += [f"{path} is missing required field {k!r}"
                   for k in schema.get("required", []) if k not in value]
        for k, sub in schema.get("properties", {}).items():
            if k in value:
                errors += validate(value[k], sub, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors


def _args(props, required):
    return {"type": "object", "properties": props, "required": required}


def _result(**ok_fields):
    """Output schema: either {"ok": true, <ok_fields>} or {"ok": false, "error": str}."""
    return {"anyOf": [
        {"type": "object", "required": ["ok", *ok_fields],
         "properties": {"ok": {"type": "boolean", "enum": [True]}, **ok_fields}},
        {"type": "object", "required": ["ok", "error"],
         "properties": {"ok": {"type": "boolean", "enum": [False]},
                        "error": {"type": "string"}}}]}


RUN_SQL = Tool(
    "run_sql", "Run ONE read-only SQLite SELECT query and return the rows "
    "(or an error message).",
    _args({"query": {"type": "string", "minLength": 1,
                     "description": "A single SELECT statement"}}, ["query"]),
    _result(columns={"type": "array", "items": {"type": "string"}},
            rows={"type": "array", "items": {"type": "array"}},
            row_count={"type": "integer"}, truncated={"type": "boolean"}))
CALCULATOR = Tool(
    "calculator", "Evaluate an arithmetic expression, e.g. "
    "'(1250 - 1000) / 1000 * 100'. Supports + - * / ** ( ) round abs min max.",
    _args({"expression": {"type": "string", "minLength": 1}}, ["expression"]),
    _result(expression={"type": "string"}, result={"type": "number"}))
DESCRIBE_TABLE = Tool(
    "describe_table", "Look up the schema. With no table, lists tables. "
    "With a table name, returns its columns.",
    _args({"table": {"type": "string", "description": "optional table name"}}, []),
    {"anyOf": [_result(tables={"type": "array", "items": {"type": "string"}}),
               _result(table={"type": "string"}, schema={"type": "string"})]})
GENERATE_SQL = Tool(
    "generate_sql", "Write a SQLite SELECT query for a data request. "
    "Returns the SQL text; run it with run_sql.",
    _args({"question": {"type": "string", "minLength": 1,
                        "description": "What data the query must return"}}, ["question"]),
    _result(sql={"type": "string", "minLength": 1}))
