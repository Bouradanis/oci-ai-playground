# OCI AI Playground — Project Brief

## What this project is

A learning project exploring AI and data capabilities on OCI Free Tier, built around
an Oracle Autonomous Database (ADB). It has grown across several phases:

- **Phase 1 (complete):** Local MCP server (stdio) for the Olist Brazilian e-commerce
  dataset — natural language SQL queries and Plotly charts via Claude Code
- **Phase 2 (complete):** Streamlit chatbot front-end calling the MCP tools over HTTP;
  OCI IAM user management tools (list users, add/remove from groups with approval flow);
  OCI Compute VM provisioning tool (free tier)
- **Phase 3 (complete):** Synthea synthetic insurance data (48M+ rows across 13 tables)
  ingested into OML_USER schema via PySpark + pandas; CARDIOTOCOGRAPHY table with an
  XGBoost model trained and stored via DBMS_DATA_MINING in CARDIO_MODEL_USER schema
- **Phase 4 (active):** Oracle Graph Studio fraud detection — building a property graph
  over the Synthea insurance data (providers, patients, payers, encounters, claims) to
  detect network fraud patterns: provider rings, phantom billing, upcoding, patient churning
- **Phase 5 (planned):** Oracle APEX front-end replacing Streamlit — chatbot UI in APEX
  backed by a FastAPI service deployed on the free-tier OCI Compute VM; APEX workspace
  points to OML_USER schema; Olist analytics dashboard (KPI cards, charts, IR reports).
  Migration work happens in `olist_copilot/` (see file structure below) — a duplicated
  copy of the app, not a move, so the root-level Streamlit app keeps running undisturbed
  until the new stack is proven and cut over.

**GRAPHUSER schema** has been created with Graph Studio access and SELECT grants on all
OML_USER tables (Olist + Cardiotocography + Synthea). Credentials are in OCI Vault
under `GRAPHUSER_SECRET_OCID`.

---

## Tech stack

| Layer | Choice |
|---|---|
| MCP framework | `mcp` Python SDK (from Anthropic) — stdio transport |
| DB driver | `oracledb` (thin mode, no Oracle Client needed) |
| OCI secrets | `oci` SDK — fetch wallet + credentials from OCI Vault |
| Plotting | `plotly` — returns HTML figures saved to temp files |
| Auth (Streamlit app) | OCI Identity Domain as OIDC provider — real OCI login, role from IAM group |
| Python env | conda (environment name: `olist_mcp`) |
| IDE | PyCharm with Claude Code plugin |

---

## OCI / ADB connection pattern

Credentials are fetched from OCI Vault at runtime. The wallet directory is set once
via `oracledb.defaults.config_dir` so it doesn't need to be passed on every connect.

```python
import oracledb
import oci, json, base64, os
from dotenv import load_dotenv

load_dotenv()
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']  # path to unzipped wallet

config = oci.config.from_file()
secrets_client = oci.secrets.SecretsClient(config)
secret_bundle = secrets_client.get_secret_bundle(os.environ['OML_USER_CREDS_SECRET_OCID'])
creds = json.loads(base64.b64decode(secret_bundle.data.secret_bundle_content.content))

connection = oracledb.connect(
    user=creds['user_name'],
    password=creds['password'],
    dsn=creds['dsn'],
)
```

**WSL-specific:** `oracledb` does not inherit `TNS_ADMIN` from Windows system env vars.
Always set `oracledb.defaults.config_dir` explicitly after `load_dotenv()`.

Do NOT hardcode credentials. Do NOT use ADS or `ads.set_auth()`.

---

## Project file structure

```
oci-ai-playground/
├── CLAUDE.md                  ← this file
├── .env                       ← TNS_ADMIN + secret OCIDs (gitignored)
├── .gitignore
├── .mcp.json                  ← MCP server definition for Claude Code
│
├── server.py                  ← MCP server entrypoint (stdio)
├── app.py                     ← Streamlit chatbot front-end (OAuth-gated, see below)
├── auth.py                    ← OCI Identity Domain OAuth login + role resolution from IAM groups
├── db/
│   ├── __init__.py
│   └── connection.py          ← get_connection() — only place that imports oracledb
├── tools/
│   ├── __init__.py
│   ├── schema.py              ← list_tables, describe_table
│   ├── query.py               ← run_query
│   └── plot.py                ← plot_query
│
├── oci_playground/
│   ├── oci_vault.ipynb        ← vault setup + connection experiments
│   └── olist_ingest.ipynb     ← one-off: CREATE tables + bulk load CSVs
│
├── olist_dataset/             ← raw Olist CSV files (gitignored)
│   ├── olist_orders_dataset.csv
│   ├── olist_order_items_dataset.csv
│   ├── olist_customers_dataset.csv
│   ├── olist_products_dataset.csv
│   ├── olist_sellers_dataset.csv
│   ├── olist_order_payments_dataset.csv
│   ├── olist_order_reviews_dataset.csv
│   ├── olist_geolocation_dataset.csv
│   └── product_category_name_translation.csv
│
└── olist_copilot/             ← Phase 5 migration target (FastAPI + APEX), see below
    ├── app.py                 ← COPY of root app.py — not wired up as the live app yet
    ├── auth.py                ← COPY of root auth.py
    ├── server.py              ← COPY of root server.py
    ├── db/
    │   ├── __init__.py
    │   └── connection.py
    └── tools/
        ├── __init__.py
        ├── schema.py
        ├── query.py
        ├── plot.py
        ├── iam.py
        ├── compute.py
        └── predict.py
```

**`olist_copilot/` is a duplicated copy, not the active app.** It was created as the
starting point for the Phase 5 FastAPI + APEX migration, so work can happen there without
touching the root-level Streamlit app that's still live (currently tunneled via ngrok for
external access). The two will diverge over time as the migration progresses.
`.mcp.json` still points at the root `server.py` — nothing currently uses the
`olist_copilot/` copy. Relative paths inside `olist_copilot/*.py` were adjusted for the
extra directory depth (e.g. `.env` and `ml/` are resolved via one extra `.parent`, since
those two things were **not** duplicated and still live only at the repo root).

---

## MCP tools (Phase 1 — complete)

### 1. `list_tables`
- No input
- Returns all tables in the schema with live row counts
- Call this first to orient before writing SQL

### 2. `describe_table`
- Input: `table_name: str`
- Returns column names, data types, nullable flags, and 3 sample rows

### 3. `run_query`
- Input: `sql: str`
- Executes an Oracle SQL SELECT, returns results as a markdown table (max 50 rows)
- On error: returns the error message + the attempted SQL

### 4. `plot_query`
- Input: `sql: str`, `chart_type: str` (bar, line, scatter, pie)
- Executes SQL, passes result to Plotly, saves chart as a temp HTML file
- Returns the file path — open with `explorer.exe "\\wsl$\Ubuntu\tmp\<file>.html"`

---

## Olist schema overview

Nine tables loaded under OML_USER schema:

```
orders (order_id PK)
  ├── order_items    (order_id FK, product_id FK, seller_id FK)
  ├── order_payments (order_id FK)
  ├── order_reviews  (order_id FK)
  └── customers      (customer_id FK) → geolocation (zip_code FK)

products (product_id PK) → product_category_translation (product_category_name FK)
sellers  (seller_id PK)  → geolocation (zip_code FK)
```

Key columns to remember:
- `orders.order_status` — delivered, shipped, canceled, etc.
- `orders.order_purchase_timestamp` — main date column
- `order_items.price` + `order_items.freight_value` — revenue components
- `order_reviews.review_score` — 1–5 integer
- `products.product_category_name` — in Portuguese (join `product_category_translation` for English)

---

## Oracle SQL conventions

This is Oracle, not PostgreSQL. Always use:
- `FETCH FIRST n ROWS ONLY` instead of `LIMIT n`
- `TO_CHAR(date_col, 'YYYY-MM')` for month grouping
- `TRUNC(date_col, 'MM')` for month truncation
- Double quotes for mixed-case identifiers if needed
- `MERGE INTO` for upserts (not `INSERT ... ON CONFLICT`)
- `SYSDATE` / `SYSTIMESTAMP` for current time

---

## Claude Code MCP config (.mcp.json)

MCP servers are defined in `.mcp.json` at the project root (not in `settings.json`).
Use the conda env's Python path directly — `conda run` intercepts stdio and breaks the server.

```json
{
  "mcpServers": {
    "olist": {
      "command": "/home/abourantanis/miniconda3/envs/olist_mcp/bin/python",
      "args": ["/mnt/c/Git_Repos/oci-ai-playground/server.py"],
      "env": {}
    }
  }
}
```

Credentials are loaded from `.env` inside `db/connection.py` — no secrets in `.mcp.json`.

---

## Claude Code workflow notes

- The MCP server is started by Claude Code via stdio automatically
- `server.py` adds its own directory to `sys.path` so imports work regardless of cwd
- `db/connection.py` is the only place that imports `oracledb`
- Keep each tool file focused — no cross-imports between tool files
- To test a tool manually: `/home/abourantanis/miniconda3/envs/olist_mcp/bin/python -c "from tools.query import run_query; print(run_query('SELECT 1 FROM DUAL'))"`

---

---

## Synthea insurance schema (OML_USER)

13 tables, 48M+ total rows. Loaded via `oci_playground/synthea_ingest.ipynb` (PySpark)
and `load_claims.py` / `load_remaining.py` (pandas + oracledb for large tables).

| Table | Rows | Notes |
|---|---|---|
| syn_patients | 22,920 | Master patient list |
| syn_encounters | 1,314,807 | Links patients → providers → organizations |
| syn_payer_transitions | 848,238 | Insurance coverage history |
| syn_conditions | 820,605 | Diagnoses |
| syn_medications | 1,102,706 | Prescriptions |
| syn_procedures | 3,649,054 | Procedures performed |
| syn_observations | 16,937,728 | Lab results, vitals |
| syn_claims | 2,417,513 | Billing claims (PK: id) |
| syn_claims_transactions | 21,345,339 | Financial transactions per claim |
| syn_immunizations | 331,463 | Vaccine records |
| syn_allergies | 21,215 | Allergy records |
| syn_careplans | 75,408 | Care plan records |
| syn_devices | 128,838 | Medical device records |

**Key fraud-detection relationships:**
- `syn_encounters.provider` → provider entity
- `syn_encounters.organization` → facility entity
- `syn_encounters` links patients ↔ providers ↔ payers
- `syn_claims` links encounters → billing
- `syn_claims_transactions` shows payment flows

**Ingest pattern for large tables (pandas + oracledb):**
```python
# Always declare ALL column dtypes explicitly — prevents pandas mixed-type inference
dtype_map = {col: (float if col in num_cols else str) for col in csv_cols}
chunk = chunk.astype(object).where(pd.notnull(chunk), None)  # NaN → None for Oracle NULL
rows  = list(chunk.itertuples(index=False, name=None))       # faster than iterrows()
cur.executemany(sql, rows)
```

---

## Graph Studio (GRAPHUSER schema)

GRAPHUSER has:
- SELECT grants on all OML_USER tables (Olist + Cardiotocography + Synthea)
- Graph Studio access via `GRAPH_DEVELOPER` role + `GRAPH$PROXY_USER`
- A sample graph `SH_PGVIEW_GRAPH` built from Oracle Sales History views (tutorial)
- Next: build a Synthea fraud-detection property graph

Connect: use `GRAPHUSER_SECRET_OCID` from `.env`.

---

## Streamlit app: OCI-authenticated RBAC login

The Streamlit app (`app.py`) requires logging in with a real OCI Identity Domain
account before any UI renders. Role (and therefore what the app allows) comes
from IAM group membership, not a separate app-level login system.

**Groups → role:**
- `olist_admins` → `admin` — full sidebar (Data + IAM + VM examples), can run IAM/VM actions
- `olist_users`  → `user` — Data examples only; IAM/VM intents are rejected
- any other/no group → blocked entirely with an error, no role assigned

**Flow (`auth.py`):**
1. App shows a "Log in with OCI →" link pointing at the Identity Domain's
   `/oauth2/v1/authorize` endpoint (Authorization Code grant, `scope=openid groups`).
2. User authenticates on OCI's own hosted login page — the app never sees a
   raw password.
3. OCI redirects back to `http://localhost:8501/?code=...&state=...`; the app
   exchanges the code for tokens at `/oauth2/v1/token`, then calls
   `/oauth2/v1/userinfo` with the access token to get `groups` claims directly
   (group objects come back as `{"id", "name", "$ref"}` — matched on `"name"`).
4. `state` is a self-verifying HMAC-signed timestamp (`auth.new_state()` /
   `auth.verify_state()`), not something stored in `st.session_state` —
   Streamlit's session doesn't reliably survive the full-page navigation
   away to OCI's login domain and back, so CSRF protection can't depend on
   server-side memory across that redirect.
5. Role gating happens twice: sidebar sections are hidden per role (UX), and
   — more importantly — `app.py`'s execution block re-checks the role before
   running any `iam_*`/`vm_*` intent, regardless of whether it was triggered
   by a sidebar button or typed directly into the free-text question box.

**Setup dependencies (Console, one-time, already done for this tenancy):**
- A Confidential Application (`olist-streamlit-app`) registered in the OCI
  Identity Domain, grant type Authorization Code + Refresh Token, redirect
  URL `http://localhost:8501` (non-HTTPS allowed explicitly for local dev),
  activated.
- `.env` additions: `OCI_OAUTH_CLIENT_ID`, `OCI_OAUTH_CLIENT_SECRET`,
  `OCI_DOMAIN_URL`, `OCI_OAUTH_REDIRECT_URI`.

**Known limitation:** the in-app "Log out" only clears `st.session_state` —
it doesn't end the actual OCI browser SSO session, so logging back in
silently re-authenticates as the same user. To test as a different account,
use a separate browser/incognito window. A real fix would redirect to the
Identity Domain's `/oauth2/v1/userlogout` with an `id_token_hint`.

**VM provisioning free-tier guard:** `tools/compute.py`'s `SHAPE_LIMITS` caps
`VM.Standard.A1.Flex` at 2 OCPU / 12 GB (Oracle lowered the Always Free A1
ceiling from the old 4 OCPU / 24 GB). `COMPARTMENT_ID`, `SUBNET_ID`,
`IMAGE_ID`, and `SSH_PUBLIC_KEY` in both `scripts/create_vm.py` and
`tools/compute.py` are read from `.env` (`COMPARTMENT_OCID`, `SUBNET_OCID`,
`IMAGE_OCID`, `SSH_PUBLIC_KEY`) rather than hardcoded — they were previously
committed as literal strings in git history on the public GitHub repo before
being moved to env vars.

---

## Phase 4 notes (active — Graph Fraud Detection)

Goal: detect insurance fraud using Oracle Graph Studio PGQL queries and graph algorithms.

**Planned graph vertices:** patients, providers, organizations, payers
**Planned graph edges:** encounters (patient→provider), claims (encounter→billing), payer transitions
**Algorithms to run:** PageRank (influential providers), Louvain (ring detection), shared-neighbor counts

---

## Phase 5 notes (planned — Oracle APEX + FastAPI chatbot deployment)

Goal: replace the Streamlit front-end with an Oracle APEX application, keeping the existing
Python chatbot logic intact by wrapping it in a FastAPI service.

### Architecture

```
Browser → APEX workspace (OML_USER schema)
               │
               │  POST /chat  {"question": "..."}
               ▼
          FastAPI service  (OCI free-tier Compute VM, same VM used in Phase 2)
               ├── intent classify  → Claude Haiku  (same logic as app.py)
               ├── sql path         → Claude Sonnet generates SQL → runs on ADB → returns JSON
               ├── iam path         → OCI Python SDK
               └── vm path          → OCI Python SDK
               │
               │  {"type": "table"|"iam"|"vm"|"message", "columns": [...], "rows": [...]}
               ▼
          APEX page (dynamic action calls apex_web_service, renders result in report region)
```

### File structure (to be created)

```
apex_api/
├── main.py          ← FastAPI app, single POST /chat endpoint
├── chat.py          ← intent classify + dispatch logic (ported from app.py)
├── requirements.txt
└── deploy.sh        ← systemd service setup on OCI VM

apex/
└── f100.sql         ← exported APEX app (version-controlled after each session)
```

### APEX app pages

| Page | Type | Content |
|---|---|---|
| 1 — Dashboard | Cards + Charts | KPI tiles, revenue by category, orders over time |
| 2 — AI Chat | Custom | Text input → REST call → display table/chart/message |
| 3 — Orders | Interactive Report | Full Olist orders with drilldown |
| 4 — Sellers | Interactive Grid | Leaderboard with faceted search |
| 5 — Delivery Estimate | Custom form | Order/shipping inputs → REST call to the GLM model → predicted delay + route map |
| 6 — IAM Admin | Interactive Report + actions | List IAM users/groups, add/remove user from group (confirm step before mutating) — admin only |
| 7 — VM Admin | Interactive Report + actions | List/create/start/stop/delete Compute VMs (confirm step before mutating) — admin only |

Pages 5–7 port the remaining Streamlit features (`tools/predict.py`, `tools/iam.py`,
`tools/compute.py`) not yet reflected above when this table was first written.

### Key decisions

- FastAPI wraps `app.py` logic almost unchanged — avoids rewriting the chatbot
- APEX workspace: `OLIST_WS`, schema: `OML_USER`
- FastAPI runs on the same free-tier VM already provisioned (ARM A1.Flex)
- Secrets (.env) stay on the VM — never passed to APEX
- APEX calls FastAPI via `apex_web_service.make_rest_request()` in a PL/SQL process
- Session state protection: all URL-passed items must be set to Restricted from day one
