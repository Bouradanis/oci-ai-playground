from db.connection import get_connection

MAX_ROWS = 50

BLOCKED_KEYWORDS = [
    'drop', 'create', 'alter', 'truncate', 'insert', 'update', 'delete',
    'merge', 'grant', 'revoke', 'commit', 'rollback', 'execute', 'exec',
    'dba_', 'v$', 'sys.', 'all_users', 'all_tables',
]


def is_safe_sql(sql: str) -> bool:
    lower = sql.strip().lower()
    if not lower.startswith('select'):
        return False
    return not any(kw in lower for kw in BLOCKED_KEYWORDS)


def run_query(sql: str) -> str:
    if not is_safe_sql(sql):
        return f"**Blocked:** only SELECT queries on Olist tables are permitted.\n\n**SQL attempted:**\n```sql\n{sql}\n```"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchmany(MAX_ROWS)
            col_names = [d[0] for d in cur.description]

        if not rows:
            return "Query returned no results."

        lines = ["| " + " | ".join(col_names) + " |"]
        lines.append("|" + "|".join(["---"] * len(col_names)) + "|")
        for row in rows:
            lines.append("| " + " | ".join("NULL" if v is None else str(v) for v in row) + " |")

        result = "\n".join(lines)
        if len(rows) == MAX_ROWS:
            result += f"\n\n_(showing first {MAX_ROWS} rows)_"
        return result

    except Exception as e:
        return f"**SQL Error:** {e}\n\n**SQL attempted:**\n```sql\n{sql}\n```"
