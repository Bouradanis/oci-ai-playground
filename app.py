import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import os
import streamlit as st
import pandas as pd
import plotly.express as px
import anthropic
from dotenv import load_dotenv

from db.connection import get_connection
from tools.iam import get_users_df, get_groups_df, add_user_to_group, remove_user_from_group

load_dotenv(Path(__file__).parent / '.env')

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="OCI Playground",
    page_icon="🔮",
    layout="wide",
)

st.title("🔮 OCI Playground")
st.caption("Ask questions about Olist data or your OCI IAM — powered by Claude + Oracle ADB")

# ── Schema context (cached at startup) ────────────────────────────────────────
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
        "\nReturn ONLY the SQL query. No explanation, no markdown, no backticks.",
    ]
    return "\n".join(lines)


# ── Claude helpers ────────────────────────────────────────────────────────────
def _claude_client():
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


INTENT_SYSTEM = """You are an intent classifier. Return JSON only, no explanation, no markdown.
Classify the user message into exactly one of:
{"intent": "sql"}                                                        -- data/analytics question about Olist e-commerce database
{"intent": "iam_users"}                                                  -- wants to list or see IAM users
{"intent": "iam_groups"}                                                 -- wants to list or see IAM groups
{"intent": "iam_add", "user": "<username>", "group": "<groupname>"}     -- add a user to a group
{"intent": "iam_remove", "user": "<username>", "group": "<groupname>"}  -- remove a user from a group"""


_IAM_KEYWORDS = ("iam", "user", "users", "group", "groups", "member", "members",
                  "add to", "remove from", "access", "permission", "role")

def classify_intent(question: str) -> dict:
    q_lower = question.lower()

    # Fast keyword pre-filter — avoids API call for obvious IAM questions
    is_iam_like = any(kw in q_lower for kw in _IAM_KEYWORDS)
    if not is_iam_like:
        return {"intent": "sql"}

    try:
        msg = _claude_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=INTENT_SYSTEM,
            messages=[{"role": "user", "content": question}],
        )
        raw = msg.content[0].text.strip()
        # Strip accidental markdown fences
        if raw.startswith("```"):
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        result = json.loads(raw)
        # Compound question → list of intents; take the first one
        if isinstance(result, list):
            return result[0] if result else {"intent": "sql"}
        return result
    except Exception:
        return {"intent": "sql"}


def generate_sql(question: str, schema_context: str) -> str:
    msg = _claude_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=schema_context,
        messages=[{"role": "user", "content": question}],
    )
    return msg.content[0].text.strip()


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
    show_sql   = st.toggle("Show generated SQL", value=True)
    max_rows   = st.slider("Max rows in table", 10, 500, 50)

    st.divider()
    st.header("💡 Data examples")
    sql_examples = [
        "What are the top 10 product categories by revenue?",
        "Show monthly order count trend in 2018",
        "Which states have the most customers?",
        "What is the average review score by product category?",
    ]
    for ex in sql_examples:
        if st.button(ex, use_container_width=True, key=f"sql_{ex[:20]}"):
            st.session_state["question"] = ex

    st.divider()
    st.header("🔐 IAM examples")
    iam_examples = [
        "Show me all IAM users",
        "List all groups",
        "Add testbiuser to ds_group",
        "Remove testbiuser from ds_group",
    ]
    for ex in iam_examples:
        if st.button(ex, use_container_width=True, key=f"iam_{ex[:20]}"):
            st.session_state["question"] = ex


# ── Main input ────────────────────────────────────────────────────────────────
question = st.text_input(
    "Ask a question",
    placeholder="e.g. Top 10 categories by revenue  —or—  Show me all IAM users",
    key="question",
)

run = st.button("Run", type="primary")

# ── Pending IAM action confirmation ──────────────────────────────────────────
if "pending_iam" in st.session_state:
    p    = st.session_state["pending_iam"]
    verb = "Add" if p["action"] == "iam_add" else "Remove"
    prep = "to" if p["action"] == "iam_add" else "from"
    st.warning(f"**Confirm:** {verb} **{p['user']}** {prep} group **{p['group']}**?")
    c1, c2, _ = st.columns([1, 1, 5])
    with c1:
        if st.button("✓ Confirm", type="primary"):
            with st.spinner("Applying change..."):
                if p["action"] == "iam_add":
                    msg = add_user_to_group(p["user"], p["group"])
                else:
                    msg = remove_user_from_group(p["user"], p["group"])
            st.success(msg)
            del st.session_state["pending_iam"]
    with c2:
        if st.button("✗ Cancel"):
            del st.session_state["pending_iam"]
            st.rerun()

# ── Execution ─────────────────────────────────────────────────────────────────
if run and question:
    with st.spinner("Classifying intent..."):
        intent = classify_intent(question)

    kind = intent.get("intent", "sql")

    # ── IAM: list users ───────────────────────────────────────────────────────
    if kind == "iam_users":
        st.session_state.pop("df", None)
        st.session_state.pop("sql", None)
        with st.spinner("Fetching IAM users..."):
            df = get_users_df()
        st.subheader(f"IAM Users ({len(df)})")
        st.dataframe(df, use_container_width=True)

    # ── IAM: list groups ──────────────────────────────────────────────────────
    elif kind == "iam_groups":
        st.session_state.pop("df", None)
        st.session_state.pop("sql", None)
        with st.spinner("Fetching IAM groups..."):
            df = get_groups_df()
        st.subheader(f"IAM Groups ({len(df)})")
        st.dataframe(df, use_container_width=True)

    # ── IAM: add / remove (store pending for confirmation) ────────────────────
    elif kind in ("iam_add", "iam_remove"):
        st.session_state.pop("df", None)
        st.session_state.pop("sql", None)
        st.session_state["pending_iam"] = {
            "action": kind,
            "user":   intent.get("user", ""),
            "group":  intent.get("group", ""),
        }
        st.rerun()

    # ── SQL / data query ──────────────────────────────────────────────────────
    else:
        schema_context = get_schema_context()
        with st.spinner("Generating SQL..."):
            try:
                sql = generate_sql(question, schema_context)
            except Exception as e:
                st.error(f"Claude API error: {e}")
                st.stop()

        with st.spinner("Querying Oracle ADB..."):
            try:
                df = run_sql(sql)
            except Exception as e:
                st.error(f"SQL error: {e}")
                st.stop()

        st.session_state["df"]  = df
        st.session_state["sql"] = sql

# ── Display SQL results (persists across sidebar interactions) ────────────────
if "df" in st.session_state and "sql" in st.session_state:
    df  = st.session_state["df"]
    sql = st.session_state["sql"]

    if show_sql:
        with st.expander("Generated SQL", expanded=True):
            st.code(sql, language="sql")

    if df.empty:
        st.warning("Query returned no results.")
    else:
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
