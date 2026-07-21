"""Text-to-SQL logic for the /query endpoint.

Reuses olist_copilot/db/connection.py for the actual DB connection (the only
place that imports oracledb, per project convention). The SELECT-only guard
and generate_sql/schema-context pattern mirror app.py's implementation
exactly (app.py's own run_sql/_FORBIDDEN_SQL, not tools/query.py — that
module's run_query returns a markdown table with no SQL-type guard of its
own, so it wasn't a fit for JSON output; noted on KAN-6).
"""
import os
import re
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

_OLIST_COPILOT_DIR = Path(__file__).parent.parent / "olist_copilot"
sys.path.insert(0, str(_OLIST_COPILOT_DIR))

from db.connection import get_connection  # noqa: E402  (path insert must run first)

load_dotenv(Path(__file__).parent.parent / '.env')

MAX_ROWS = 50

_FORBIDDEN_SQL = re.compile(
    r'^\s*(DROP|DELETE|TRUNCATE|INSERT|UPDATE|CREATE|ALTER|GRANT|REVOKE|MERGE)\b',
    re.IGNORECASE,
)

_schema_context_cache: str | None = None


def _claude_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def get_schema_context() -> str:
    global _schema_context_cache
    if _schema_context_cache is not None:
        return _schema_context_cache

    conn = get_connection()
    lines = ["You are a SQL expert connected to an Oracle Autonomous Database.",
             "The schema is OML_USER. Tables available:\n"]
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            cur.execute("""
                SELECT column_name, data_type
                FROM user_tab_columns
                WHERE table_name = :1
                ORDER BY column_id
            """, [table])
            cols = ", ".join(f"{r[0]} ({r[1]})" for r in cur.fetchall())
            lines.append(f"  {table}: {cols}")
    lines += [
        "\nOracle SQL rules — always follow these:",
        "- Use FETCH FIRST n ROWS ONLY, never LIMIT",
        "- Use TO_CHAR(date_col, 'YYYY-MM') for month grouping",
        "- Use TRUNC(date_col, 'MM') for month truncation",
        "- Use SYSDATE for current date",
        "- product_category_name is in Portuguese — join product_category_translation for English",
        "\nReturn ONLY the SQL query. No explanation, no markdown, no backticks.",
    ]
    _schema_context_cache = "\n".join(lines)
    return _schema_context_cache


def generate_sql(question: str) -> str:
    msg = _claude_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=get_schema_context(),
        messages=[{"role": "user", "content": question}],
    )
    sql = msg.content[0].text.strip()
    # Defensive: strip accidental markdown fences, same as app.py's intent parsing
    if sql.startswith("```"):
        sql = sql.strip("`")
        if sql.lower().startswith("sql"):
            sql = sql[3:]
        sql = sql.strip()
    return sql


def _jsonify(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):  # datetime/date
        return value.isoformat()
    return value


def run_sql(sql: str) -> dict:
    if _FORBIDDEN_SQL.search(sql):
        raise ValueError("Only SELECT queries are allowed.")
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchmany(MAX_ROWS)
        cols = [d[0] for d in cur.description]
    return {
        "columns": cols,
        "rows": [[_jsonify(v) for v in row] for row in rows],
    }


def answer_question(question: str) -> dict:
    """End-to-end: NL question -> generated SQL -> executed -> JSON result.

    Returns a dict shaped either as a success:
      {"sql": "...", "columns": [...], "rows": [[...], ...], "error": None}
    or an error (SQL generation succeeded but execution/guard failed):
      {"sql": "...", "columns": None, "rows": None, "error": "..."}
    """
    sql = generate_sql(question)
    try:
        result = run_sql(sql)
        return {"sql": sql, "columns": result["columns"], "rows": result["rows"], "error": None}
    except Exception as e:
        return {"sql": sql, "columns": None, "rows": None, "error": str(e)}