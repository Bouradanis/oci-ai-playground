import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import os
import streamlit as st
import pandas as pd
import plotly.express as px
import anthropic
from dotenv import load_dotenv

from db.connection import get_connection
from tools.query import is_safe_sql

load_dotenv(Path(__file__).parent / '.env')

# ── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Olist Analytics",
    page_icon="📦",
    layout="wide",
)

st.title("📦 Olist Analytics")
st.caption("Ask questions about Brazilian e-commerce data — powered by Claude + Oracle ADB")

# ── Schema context (cached at startup) ───────────────────────────────────────
@st.cache_resource
def get_schema_context() -> str:
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
        "\nSecurity rules — strictly enforce these:",
        "- Only generate SELECT queries. NEVER generate DDL (CREATE, DROP, ALTER, TRUNCATE) or DML (INSERT, UPDATE, DELETE, MERGE).",
        "- NEVER query system tables (ALL_TABLES, USER_TABLES, DBA_*, V$*, SYS.*).",
        "- If the question is not about Olist business data, reply with exactly: NOT_A_BUSINESS_QUESTION",
        "\nReturn ONLY the SQL query. No explanation, no markdown, no backticks.",
    ]
    return "\n".join(lines)


# ── SQL generation via Claude API ─────────────────────────────────────────────
def generate_sql(question: str, schema_context: str) -> str:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=schema_context,
        messages=[{"role": "user", "content": question}],
    )
    return message.content[0].text.strip()


# ── Query execution ───────────────────────────────────────────────────────────
def run_sql(sql: str) -> pd.DataFrame:
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchmany(500)
        cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")
    chart_type = st.selectbox("Chart type", ["bar", "line", "scatter", "pie"])
    show_sql = st.toggle("Show generated SQL", value=True)
    max_rows = st.slider("Max rows in table", 10, 500, 50)
    st.divider()
    st.header("💡 Example questions")
    examples = [
        "What are the top 10 product categories by revenue?",
        "Show monthly order count trend in 2018",
        "Which states have the most customers?",
        "What is the average review score by product category?",
        "Show the distribution of payment types",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state["question"] = ex

# ── Main input ────────────────────────────────────────────────────────────────
question = st.text_input(
    "Ask a question about the Olist data",
    placeholder="e.g. What are the top 10 product categories by revenue?",
    key="question",
)

run = st.button("Run", type="primary", use_container_width=False)

# ── Execution ─────────────────────────────────────────────────────────────────
if run and question:
    schema_context = get_schema_context()

    with st.spinner("Generating SQL..."):
        try:
            sql = generate_sql(question, schema_context)
        except Exception as e:
            st.error(f"Claude API error: {e}")
            st.stop()

    if sql == "NOT_A_BUSINESS_QUESTION":
        st.warning("I can only answer business questions about the Olist e-commerce data.")
        st.stop()

    if not is_safe_sql(sql):
        st.error(f"Blocked: only SELECT queries on Olist tables are permitted.")
        st.stop()

    with st.spinner("Querying Oracle ADB..."):
        try:
            df = run_sql(sql)
        except Exception as e:
            st.error(f"SQL error: {e}")
            st.stop()

    if df.empty:
        st.warning("Query returned no results.")
        st.stop()

    # Store results in session state so sidebar changes don't wipe them
    st.session_state["df"] = df
    st.session_state["sql"] = sql

# ── Display results (persists across sidebar interactions) ────────────────────
if "df" in st.session_state:
    df = st.session_state["df"]
    sql = st.session_state["sql"]

    if show_sql:
        with st.expander("Generated SQL", expanded=True):
            st.code(sql, language="sql")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Results")
        st.dataframe(df.head(max_rows), use_container_width=True)
        st.caption(f"{len(df):,} rows fetched · showing {min(max_rows, len(df)):,}")

    with col2:
        st.subheader("Chart")
        if len(df.columns) >= 2:
            try:
                x_col, y_col = df.columns[0], df.columns[1]
                if chart_type == "bar":
                    fig = px.bar(df.head(max_rows), x=x_col, y=y_col)
                elif chart_type == "line":
                    fig = px.line(df.head(max_rows), x=x_col, y=y_col)
                elif chart_type == "scatter":
                    fig = px.scatter(df.head(max_rows), x=x_col, y=y_col)
                elif chart_type == "pie":
                    fig = px.pie(df.head(max_rows), names=x_col, values=y_col)
                st.plotly_chart(fig, use_container_width=True)
            except Exception as e:
                st.warning(f"Could not render chart: {e}")
        else:
            st.info("Need at least 2 columns to plot.")
