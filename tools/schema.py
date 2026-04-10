from db.connection import get_connection


def list_tables() -> str:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM user_tables ORDER BY table_name")
        tables = [row[0] for row in cur.fetchall()]

    lines = ["| Table | Rows |", "|-------|------|"]
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            lines.append(f"| {table} | {count:,} |")

    return "\n".join(lines)


def describe_table(table_name: str) -> str:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, nullable, data_length, data_precision, data_scale
            FROM user_tab_columns
            WHERE table_name = UPPER(:1)
            ORDER BY column_id
        """, [table_name])
        cols = cur.fetchall()

    if not cols:
        return f"Table '{table_name}' not found."

    lines = [f"### {table_name.upper()}\n"]
    lines.append("| Column | Type | Nullable |")
    lines.append("|--------|------|----------|")
    for col_name, data_type, nullable, data_length, precision, scale in cols:
        if data_type in ('VARCHAR2', 'CHAR', 'NVARCHAR2'):
            type_str = f"{data_type}({data_length})"
        elif data_type == 'NUMBER' and precision:
            type_str = f"NUMBER({precision},{scale or 0})"
        else:
            type_str = data_type
        lines.append(f"| {col_name} | {type_str} | {'YES' if nullable == 'Y' else 'NO'} |")

    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table_name} FETCH FIRST 3 ROWS ONLY")
        sample_rows = cur.fetchall()
        col_names = [d[0] for d in cur.description]

    lines.append("\n**Sample rows:**\n")
    lines.append("| " + " | ".join(col_names) + " |")
    lines.append("|" + "|".join(["---"] * len(col_names)) + "|")
    for row in sample_rows:
        lines.append("| " + " | ".join("NULL" if v is None else str(v) for v in row) + " |")

    return "\n".join(lines)
