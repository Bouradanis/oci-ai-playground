# Olist MCP Server — Project Brief

## What this project is

A learning project to build a local MCP (Model Context Protocol) server in Python,
connected to an Oracle Autonomous Database (ADB) on OCI Free Tier. The dataset is
the Brazilian E-Commerce Public Dataset by Olist (Kaggle), loaded into Oracle ADB.

The MCP server exposes tools that let Claude (via Claude Code) answer natural language
questions about the Olist data — querying, explaining schema, and generating plots.

This is a **Phase 1 (local stdio)** build. No HTTP server, no deployment yet.

---

## Tech stack

| Layer | Choice |
|---|---|
| MCP framework | `mcp` Python SDK (from Anthropic) — stdio transport |
| DB driver | `oracledb` (thin mode, no Oracle Client needed) |
| OCI secrets | `oci` SDK — fetch wallet + credentials from OCI Vault |
| Plotting | `plotly` — returns HTML figures saved to temp files |
| Python env | conda (environment name: `olist-mcp`) |
| IDE | PyCharm with Claude Code plugin |

---

## OCI / ADB connection pattern

Use the **thin client wallet approach** — download the wallet zip from OCI Vault or
load it from a local path, then connect via `oracledb.connect()` with the wallet dir.

```python
import oracledb
import os

connection = oracledb.connect(
    user=os.environ["ADB_USER"],
    password=os.environ["ADB_PASSWORD"],
    dsn=os.environ["ADB_DSN"],           # e.g. "mydb_high"
    config_dir=os.environ["WALLET_DIR"], # path to unzipped wallet
    wallet_location=os.environ["WALLET_DIR"],
    wallet_password=os.environ["WALLET_PASSWORD"],
)
```

Credentials come from environment variables (set in `.env`, never committed).
Do NOT hardcode credentials. Do NOT use ADS or `ads.set_auth()` — this is a
local project, not an OCI notebook session.

---

## Project file structure

```
olist-mcp/
├── CLAUDE.md              ← this file
├── .env                   ← secrets (gitignored)
├── .gitignore
├── requirements.txt
├── README.md
│
├── server.py              ← MCP server entrypoint (stdio)
├── tools/
│   ├── __init__.py
│   ├── schema.py          ← list_tables, describe_table tools
│   ├── query.py           ← text_to_sql tool
│   └── plot.py            ← plot_query tool
│
├── db/
│   ├── __init__.py
│   └── connection.py      ← single shared get_connection() function
│
├── data/
│   └── olist/             ← raw Olist CSV files (gitignored if large)
│       ├── olist_orders_dataset.csv
│       ├── olist_order_items_dataset.csv
│       ├── olist_customers_dataset.csv
│       ├── olist_products_dataset.csv
│       ├── olist_sellers_dataset.csv
│       ├── olist_order_payments_dataset.csv
│       ├── olist_order_reviews_dataset.csv
│       └── olist_geolocation_dataset.csv
│
├── scripts/
│   └── load_data.py       ← one-off script to CREATE tables and bulk load CSVs
│
└── docs/
    └── schema.md          ← Olist table descriptions and key relationships
```

---

## MCP tools to build (Phase 1)

### 1. `list_tables`
- No input
- Returns list of table names in the schema with row counts
- Used by Claude to orient itself before writing SQL

### 2. `describe_table`
- Input: `table_name: str`
- Returns column names, types, nullable flags, and 3 sample rows
- Used by Claude to understand columns before querying

### 3. `text_to_sql`
- Input: `question: str`
- Claude (inside the tool) generates SQL from the question + schema context
- Executes the SQL against ADB
- Returns results as a markdown table (max 50 rows)
- On SQL error: return the error message + the attempted SQL so the user can debug

### 4. `plot_query`
- Input: `question: str`, `chart_type: str` (bar, line, scatter, pie)
- Same text-to-SQL flow as above
- Passes result DataFrame to Plotly
- Saves chart as a temp HTML file, returns the file path
- Claude Code can open/display it

---

## Olist schema overview

Eight tables with the following key relationships:

```
orders (order_id PK)
  ├── order_items    (order_id FK, product_id FK, seller_id FK)
  ├── order_payments (order_id FK)
  ├── order_reviews  (order_id FK)
  └── customers      (customer_id FK) → geolocation (zip_code FK)

products (product_id PK)
sellers  (seller_id PK) → geolocation (zip_code FK)
```

Key columns to remember:
- `orders.order_status` — delivered, shipped, canceled, etc.
- `orders.order_purchase_timestamp` — main date column
- `order_items.price` + `order_items.freight_value` — revenue components
- `order_reviews.review_score` — 1–5 integer
- `products.product_category_name` — in Portuguese

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

## Claude Code workflow notes

- Always activate the `olist-mcp` conda environment before running anything
- The MCP server is started by Claude Code via stdio — it should NOT have a
  `if __name__ == "__main__": uvicorn.run(...)` block in Phase 1
- Test individual tools with `python -c "from tools.query import text_to_sql; ..."`
  before wiring into the MCP server
- Keep each tool file focused — no cross-imports between tool files
- `db/connection.py` is the only place that imports `oracledb`

---

## Claude Code settings.json entry (for reference)

```json
{
  "mcpServers": {
    "olist": {
      "command": "conda",
      "args": ["run", "-n", "olist-mcp", "python", "/mnt/c/Git_Repos/oci-ai-playground/server.py"],
      "env": {
        "ADB_USER": "...",
        "ADB_PASSWORD": "...",
        "ADB_DSN": "...",
        "WALLET_DIR": "...",
        "WALLET_PASSWORD": "..."
      }
    }
  }
}
```

(Replace `/path/to/olist-mcp/` with the actual absolute path on your machine.)

---

## Phase 2 notes (not building yet)

- Switch transport from stdio to HTTP/SSE using FastAPI + `mcp.server.fastapi`
- Add a Streamlit front-end that calls the MCP tools via HTTP
- Consider deploying the FastAPI server as an OCI Compute instance or Oracle Function