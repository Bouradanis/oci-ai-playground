import sys
import json
import re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import os
import streamlit as st
import pandas as pd
import plotly.express as px
import folium
from streamlit_folium import st_folium
import anthropic
from dotenv import load_dotenv

import auth
from db.connection import get_connection
from tools.iam import get_users_df, get_groups_df, add_user_to_group, remove_user_from_group
from tools.compute import get_vms_df, create_vm, start_vm, stop_vm, delete_vm
from tools.predict import predict_delivery_delay, get_dropdown_options, BRAZIL_STATE_COORDS

load_dotenv(Path(__file__).parent.parent / '.env')

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Olist Copilot",
    page_icon="🔮",
    layout="wide",
)

# ── Auth gate: log in with real OCI credentials, role from IAM group ──────────
if "identity" not in st.session_state:
    params = st.query_params
    if "code" in params:
        if not auth.verify_state(params.get("state", "")):
            st.error("Login failed: state invalid or expired. Please try logging in again.")
            st.query_params.clear()
            st.stop()
        try:
            st.session_state["identity"] = auth.login(params["code"])
        except Exception as e:
            st.error(f"Login failed: {e}")
            st.query_params.clear()
            st.stop()
        st.query_params.clear()
        st.rerun()
    else:
        state = auth.new_state()
        st.title("🔮 Olist Copilot")
        st.caption("Ask questions about Olist data or your OCI IAM — powered by Claude + Oracle ADB")
        st.markdown(f"[**Log in with OCI →**]({auth.build_authorize_url(state)})")
        st.stop()

identity = st.session_state["identity"]
if identity["role"] is None:
    st.error(f"**{identity['username']}** isn't a member of `olist_admins` or `olist_users` — "
             f"ask an admin to add you to one of those groups.")
    st.session_state.pop("identity", None)
    st.markdown(f"[**Log out →**]({auth.build_logout_url(identity.get('id_token'))})")
    st.stop()

is_admin = identity["role"] == "admin"

st.title("🔮 Olist Copilot")
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
        "\nUseful Oracle system views (not in user_tables but always available):",
        "  user_mining_models: model_name, algorithm, mining_function, build_duration — lists trained ML models",
        "  all_mining_models: same but includes models from other schemas (e.g. CARDIO_MODEL_USER)",
        "  user_tab_columns: column metadata",
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
{"intent": "iam_remove", "user": "<username>", "group": "<groupname>"}  -- remove a user from a group
{"intent": "vm_list"}                                                            -- list/show VMs or instances
{"intent": "vm_create", "name": "<display_name>", "ocpus": <n>, "memory_gb": <n>} -- create a free tier VM (default: E2.1.Micro, 1 OCPU, 1GB -- always available, no capacity waits)
{"intent": "vm_start", "name": "<vm_name>"}                                      -- start/activate a stopped VM
{"intent": "vm_stop", "name": "<vm_name>"}                                       -- stop/deactivate a running VM
{"intent": "vm_delete", "name": "<vm_name>"}                                     -- delete/terminate a VM"""


_IAM_KEYWORDS = ("iam", "user", "users", "group", "groups", "member", "members",
                  "add to", "remove from", "access", "permission", "role",
                  "vm", "instance", "virtual machine", "compute", "server",
                  "start", "stop", "delete", "terminate", "create vm", "launch")

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


_FORBIDDEN_SQL = re.compile(
    r'^\s*(DROP|DELETE|TRUNCATE|INSERT|UPDATE|CREATE|ALTER|GRANT|REVOKE|MERGE)\b',
    re.IGNORECASE,
)

def run_sql(sql: str) -> pd.DataFrame:
    if _FORBIDDEN_SQL.search(sql):
        raise ValueError("Only SELECT queries are allowed.")
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql)
        rows = cur.fetchmany(500)
        cols = [d[0] for d in cur.description]
    return pd.DataFrame(rows, columns=cols)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.caption(f"Logged in as **{identity['username']}** ({identity['role']})")
    st.markdown(f"[Log out →]({auth.build_logout_url(identity.get('id_token'))})")
    st.divider()

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

    if is_admin:
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

        st.divider()
        st.header("🖥️ VM examples")
        vm_examples = [
            "Show my VMs",
            "Create a VM called test-vm",
            "Start test-vm",
            "Stop test-vm",
            "Delete test-vm",
        ]
        for ex in vm_examples:
            if st.button(ex, use_container_width=True, key=f"vm_{ex[:20]}"):
                st.session_state["question"] = ex


# ── Main area: chat vs. delivery estimate tabs ─────────────────────────────────
chat_tab, estimate_tab = st.tabs(["💬 Chat", "🚚 Delivery Estimate"])

with chat_tab:
    # ── Main input ────────────────────────────────────────────────────────────
    question = st.text_input(
        "Ask a question",
        placeholder="e.g. Top 10 categories by revenue  —or—  Show me all IAM users",
        key="question",
    )

    run = st.button("Run", type="primary")

    # ── Pending compute action confirmation ───────────────────────────────────
    if "pending_compute" in st.session_state:
        p    = st.session_state["pending_compute"]
        action = p["action"]
        name   = p.get("name", "")
        if action == "vm_create":
            msg = f"Create VM **{name}** ({p.get('shape','VM.Standard.E2.1.Micro')} · {p.get('ocpus',1)} OCPU · {p.get('memory_gb',1)}GB)"
        elif action == "vm_start":
            msg = f"Start VM **{name}**?"
        elif action == "vm_stop":
            msg = f"Stop VM **{name}**?"
        elif action == "vm_delete":
            msg = f"⚠️ Permanently delete VM **{name}**?"
        else:
            msg = f"{action} **{name}**?"
        st.warning(msg)
        c1, c2, _ = st.columns([1, 1, 5])
        with c1:
            if st.button("✓ Confirm", type="primary", key="confirm_compute"):
                with st.spinner("Applying..."):
                    if action == "vm_create":
                        result = create_vm(name, p.get("shape", "VM.Standard.E2.1.Micro"),
                                           p.get("ocpus", 1), p.get("memory_gb", 1))
                    elif action == "vm_start":
                        result = start_vm(name)
                    elif action == "vm_stop":
                        result = stop_vm(name)
                    elif action == "vm_delete":
                        result = delete_vm(name)
                    else:
                        result = "Unknown action"
                st.success(result)
                del st.session_state["pending_compute"]
        with c2:
            if st.button("✗ Cancel", key="cancel_compute"):
                del st.session_state["pending_compute"]
                st.rerun()

    # ── Pending IAM action confirmation ───────────────────────────────────────
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

    # ── Execution ──────────────────────────────────────────────────────────────
    if run and question:
        with st.spinner("Classifying intent..."):
            intent = classify_intent(question)

        kind = intent.get("intent", "sql")

        # ── Role guard: IAM/VM actions require the admin role, regardless of how
        #    the intent was triggered (sidebar button or free-typed question) ──────
        if kind != "sql" and not is_admin:
            st.session_state.pop("df", None)
            st.session_state.pop("sql", None)
            st.error("Your account doesn't have permission for IAM/VM actions. "
                     "Ask an admin to add you to `olist_admins`.")
            st.stop()

        # ── IAM: list users ───────────────────────────────────────────────────
        if kind == "iam_users":
            st.session_state.pop("df", None)
            st.session_state.pop("sql", None)
            with st.spinner("Fetching IAM users..."):
                df = get_users_df()
            st.subheader(f"IAM Users ({len(df)})")
            st.dataframe(df, use_container_width=True)

        # ── IAM: list groups ──────────────────────────────────────────────────
        elif kind == "iam_groups":
            st.session_state.pop("df", None)
            st.session_state.pop("sql", None)
            with st.spinner("Fetching IAM groups..."):
                df = get_groups_df()
            st.subheader(f"IAM Groups ({len(df)})")
            st.dataframe(df, use_container_width=True)

        # ── IAM: add / remove (store pending for confirmation) ────────────────
        elif kind in ("iam_add", "iam_remove"):
            st.session_state.pop("df", None)
            st.session_state.pop("sql", None)
            st.session_state["pending_iam"] = {
                "action": kind,
                "user":   intent.get("user", ""),
                "group":  intent.get("group", ""),
            }
            st.rerun()

        # ── VM: list ───────────────────────────────────────────────────────────
        elif kind == "vm_list":
            st.session_state.pop("df", None)
            st.session_state.pop("sql", None)
            with st.spinner("Fetching VMs..."):
                df = get_vms_df()
            st.subheader(f"Compute Instances ({len(df)})")
            st.dataframe(df.drop(columns=['id']), use_container_width=True)

        # ── VM: create / start / stop / delete (confirmation required) ─────────
        elif kind in ("vm_create", "vm_start", "vm_stop", "vm_delete"):
            st.session_state.pop("df", None)
            st.session_state.pop("sql", None)
            st.session_state["pending_compute"] = {
                "action":    kind,
                "name":      intent.get("name", "olist-mcp-vm"),
                "shape":     intent.get("shape", "VM.Standard.E2.1.Micro"),
                "ocpus":     intent.get("ocpus", 1),
                "memory_gb": intent.get("memory_gb", 1),
            }
            st.rerun()

        # ── SQL / data query ────────────────────────────────────────────────────
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

    # ── Display SQL results (persists across sidebar interactions) ─────────────
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

with estimate_tab:
    st.caption("Predict delivery delay for a hypothetical order — checkout-time features, GLM model (`DELIVERY_DELAY_GLM_FINAL`)")
    dropdown_options = get_dropdown_options()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("**Order**")
        pred_category = st.selectbox("Product category", dropdown_options["PRIMARY_PRODUCT_CATEGORY_NAME_ENGLISH"])
        pred_promised_days = st.number_input("Promised delivery window (days)", min_value=1, max_value=90, value=20)
        pred_num_items = st.number_input("Number of items", min_value=1, max_value=20, value=1)
    with col2:
        st.markdown("**Shipping**")
        pred_customer_state = st.selectbox("Customer state", dropdown_options["CUSTOMER_STATE"])
        pred_seller_state = st.selectbox("Seller state", dropdown_options["PRIMARY_SELLER_STATE"])
        pred_distance_km = st.number_input("Approx. shipping distance (km)", min_value=0.0, max_value=4000.0, value=400.0, step=50.0)
    with col3:
        st.markdown("**Cost & weight**")
        pred_total_price = st.number_input("Total price (R$)", min_value=0.0, value=150.0, step=10.0)
        pred_total_freight = st.number_input("Total freight value (R$)", min_value=0.0, value=25.0, step=5.0)
        pred_total_weight = st.number_input("Total weight (g)", min_value=0.0, value=1000.0, step=100.0)

    st.markdown("**Heaviest item dimensions**")
    col4, col5, col6, col7 = st.columns(4)
    with col4:
        pred_heaviest_weight = st.number_input("Weight (g)", min_value=0.0, value=1000.0, step=100.0, key="pred_hw")
    with col5:
        pred_heaviest_length = st.number_input("Length (cm)", min_value=0.0, value=20.0, step=1.0, key="pred_hl")
    with col6:
        pred_heaviest_height = st.number_input("Height (cm)", min_value=0.0, value=10.0, step=1.0, key="pred_hh")
    with col7:
        pred_heaviest_width = st.number_input("Width (cm)", min_value=0.0, value=15.0, step=1.0, key="pred_hwi")

    if st.button("Estimate Delay", type="primary"):
        features = {
            "PROMISED_DELIVERY_DAYS": float(pred_promised_days),
            "NUM_ITEMS": int(pred_num_items),
            "TOTAL_PRICE": float(pred_total_price),
            "TOTAL_FREIGHT_VALUE": float(pred_total_freight),
            "TOTAL_WEIGHT_G": float(pred_total_weight),
            "PRIMARY_PRODUCT_CATEGORY_NAME_ENGLISH": pred_category,
            "PRIMARY_SELLER_STATE": pred_seller_state,
            "HEAVIEST_PRODUCT_WEIGHT_G": float(pred_heaviest_weight),
            "HEAVIEST_PRODUCT_LENGTH_CM": float(pred_heaviest_length),
            "HEAVIEST_PRODUCT_HEIGHT_CM": float(pred_heaviest_height),
            "HEAVIEST_PRODUCT_WIDTH_CM": float(pred_heaviest_width),
            "CUSTOMER_STATE": pred_customer_state,
            "SAME_STATE_FLAG": 1 if pred_customer_state == pred_seller_state else 0,
            "MAX_DISTANCE_KM": float(pred_distance_km),
        }
        st.session_state.pop("delivery_prediction_error", None)
        with st.spinner("Scoring..."):
            try:
                st.session_state["delivery_prediction"] = predict_delivery_delay(features)
                # Save the states used for THIS prediction, not whatever the form
                # currently shows -- keeps the map in sync with the displayed result
                # even if the user changes the dropdowns afterward without resubmitting.
                st.session_state["delivery_prediction_states"] = (pred_customer_state, pred_seller_state)
            except Exception as e:
                st.session_state.pop("delivery_prediction", None)
                st.session_state["delivery_prediction_error"] = str(e)

    if "delivery_prediction" in st.session_state:
        pred_days = st.session_state["delivery_prediction"]
        st.divider()
        if pred_days > 0:
            st.metric("Predicted delivery delay", f"{pred_days:.1f} days late")
        else:
            st.metric("Predicted delivery delay", f"{abs(pred_days):.1f} days early")
        st.caption("Predicted at checkout time (GLM model, DELIVERY_DELAY_GLM_FINAL) — "
                   "positive means later than promised, negative means earlier.")

        result_customer_state, result_seller_state = st.session_state["delivery_prediction_states"]
        cust_lat, cust_lon = BRAZIL_STATE_COORDS[result_customer_state]
        sell_lat, sell_lon = BRAZIL_STATE_COORDS[result_seller_state]

        midpoint = ((cust_lat + sell_lat) / 2, (cust_lon + sell_lon) / 2)
        # fit_bounds() is unreliable inside the st_folium iframe (Leaflet computes it before
        # the container has its final width, so it often falls back to a whole-world zoom) --
        # picking zoom from the span between the two points instead is more predictable here.
        span_deg = max(abs(cust_lat - sell_lat), abs(cust_lon - sell_lon))
        if span_deg < 2:
            zoom = 7
        elif span_deg < 5:
            zoom = 6
        elif span_deg < 10:
            zoom = 5
        elif span_deg < 20:
            zoom = 4
        else:
            zoom = 3
        route_map = folium.Map(location=midpoint, zoom_start=zoom, tiles="CartoDB positron")
        folium.Marker(
            [sell_lat, sell_lon], tooltip=f"Seller ({result_seller_state})",
            icon=folium.Icon(color="blue"),
        ).add_to(route_map)
        folium.Marker(
            [cust_lat, cust_lon], tooltip=f"Customer ({result_customer_state})",
            icon=folium.Icon(color="red"),
        ).add_to(route_map)
        folium.PolyLine(
            [[sell_lat, sell_lon], [cust_lat, cust_lon]], color="crimson", weight=2.5,
        ).add_to(route_map)

        st.caption("Route shown is state-to-state (capital coordinates), not a precise address-to-address distance.")
        st_folium(route_map, use_container_width=True, height=350, returned_objects=[])
    elif "delivery_prediction_error" in st.session_state:
        st.error(f"Prediction failed: {st.session_state['delivery_prediction_error']}")