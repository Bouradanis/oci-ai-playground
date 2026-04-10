# Olist MCP Server — Project Brief

## What this project is

A learning project to build a local MCP (Model Context Protocol) server in Python,
connected to an Oracle Autonomous Database (ADB) on OCI Free Tier. The dataset is
the Brazilian E-Commerce Public Dataset by Olist (Kaggle), loaded into Oracle ADB.

The MCP server exposes tools that let Claude (via Claude Code) answer natural language
questions about the Olist data — querying, explaining schema, and generating plots.

**Phase 1 (local stdio) is complete and working.**

---

## Tech stack

| Layer | Choice |
|---|---|
| MCP framework | `mcp` Python SDK (from Anthropic) — stdio transport |
| DB driver | `oracledb` (thin mode, no Oracle Client needed) |
| OCI secrets | `oci` SDK — fetch wallet + credentials from OCI Vault |
| Plotting | `plotly` — returns HTML figures saved to temp files |
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
└── olist_dataset/             ← raw Olist CSV files (gitignored)
    ├── olist_orders_dataset.csv
    ├── olist_order_items_dataset.csv
    ├── olist_customers_dataset.csv
    ├── olist_products_dataset.csv
    ├── olist_sellers_dataset.csv
    ├── olist_order_payments_dataset.csv
    ├── olist_order_reviews_dataset.csv
    ├── olist_geolocation_dataset.csv
    └── product_category_name_translation.csv
```

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

## Phase 2 notes (not building yet)

- Switch transport from stdio to HTTP/SSE using FastAPI + `mcp.server.fastapi`
- Add a Streamlit front-end that calls the MCP tools via HTTP
- Consider deploying the FastAPI server as an OCI Compute instance or Oracle Function
