import tempfile

import pandas as pd
import plotly.express as px

from db.connection import get_connection

CHART_BUILDERS = {
    'bar':     lambda df: px.bar(df, x=df.columns[0], y=df.columns[1]),
    'line':    lambda df: px.line(df, x=df.columns[0], y=df.columns[1]),
    'scatter': lambda df: px.scatter(df, x=df.columns[0], y=df.columns[1]),
    'pie':     lambda df: px.pie(df, names=df.columns[0], values=df.columns[1]),
}


def plot_query(sql: str, chart_type: str) -> str:
    if chart_type not in CHART_BUILDERS:
        return f"Unknown chart_type '{chart_type}'. Valid options: {', '.join(CHART_BUILDERS)}"

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            col_names = [d[0] for d in cur.description]

        if not rows:
            return "Query returned no data to plot."

        df = pd.DataFrame(rows, columns=col_names)
        fig = CHART_BUILDERS[chart_type](df)

        tmp = tempfile.NamedTemporaryFile(suffix='.html', delete=False)
        fig.write_html(tmp.name)
        return f"Chart saved to: {tmp.name}\nOpen this file in a browser to view it."

    except Exception as e:
        return f"**Error:** {e}\n\n**SQL attempted:**\n```sql\n{sql}\n```"
