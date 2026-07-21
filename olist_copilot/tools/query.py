from db.connection import get_connection

MAX_ROWS = 50


def run_query(sql: str) -> str:
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
