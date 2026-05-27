---
name: graph-fraud
description: Guide for Oracle Graph Studio fraud detection on Synthea insurance data — building the synthea_fraud_graph property graph in GRAPHUSER, running PGQL pattern queries (provider rings, upcoding, patient churning), and executing graph algorithms (PageRank, Louvain, WCC) via opg4py.
disable-model-invocation: true
---

# Oracle Graph Studio — Synthea Fraud Detection

You are helping build and query an Oracle property graph over the Synthea synthetic
insurance dataset stored in OML_USER schema on Oracle Autonomous Database (ADB).
The graph lives in GRAPHUSER schema. The goal is to detect network fraud patterns:
provider rings, phantom billing, upcoding, and patient churning.

---

## Context you must keep in mind

### Schemas
- **OML_USER** — owns all 13 Synthea tables (READ ONLY from GRAPHUSER)
- **GRAPHUSER** — the graph schema; has SELECT grants on all OML_USER tables;
  has `GRAPH_DEVELOPER` role + `GRAPH$PROXY_USER`

### Connection pattern (GRAPHUSER)
```python
import oci, json, base64, os, oracledb
from dotenv import load_dotenv

load_dotenv('/mnt/c/Git_Repos/oci-ai-playground/.env')
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']

config = oci.config.from_file()
secrets_client = oci.secrets.SecretsClient(config)
bundle = secrets_client.get_secret_bundle(os.environ['GRAPHUSER_SECRET_OCID'])
creds = json.loads(base64.b64decode(bundle.data.secret_bundle_content.content).decode())

conn = oracledb.connect(user=creds['user_name'], password=creds['password'], dsn=creds['dsn'])
```

### Key Synthea tables (all in OML_USER, queried via GRAPHUSER)
```
syn_patients          22,920 rows   — id (PK), gender, dob, city, state, zip
syn_encounters     1,314,807 rows   — id (PK), patient, provider, organization, payer,
                                      encounterclass, start, stop, base_encounter_cost,
                                      total_claim_cost, payer_coverage, reasoncode
syn_payer_transitions  848,238 rows — patient, start_year, end_year, payer, member_id
syn_claims          2,417,513 rows  — id (PK), patient_id, provider_id,
                                      primary_patient_insurance_id, service_date,
                                      outstanding1, outstanding2, outstandingp,
                                      healthcare_claim_type_id1
syn_claims_transactions 21.3M rows — id (PK), claim_id, patient_id, txn_type,
                                      amount, from_date, procedure_code, units,
                                      payments, adjustments, outstanding
syn_conditions        820,605 rows  — patient, encounter, start, stop, code, description
syn_procedures      3,649,054 rows  — patient, encounter, date, code, description,
                                      base_cost
```

---

## Oracle Graph — key facts for ADB

1. **In-database graphs** — Oracle ADB 23ai supports `CREATE PROPERTY GRAPH` as SQL DDL.
   No separate graph server process needed for DDL or PGQL queries.

2. **Graph Studio UI** — accessed via ADB Console → "Graph Studio" link.
   Use it to: write PGQL notebooks, visualise sub-graphs, run algorithms interactively.

3. **SQL/PGQL from Python** — run PGQL through `oracledb` using the
   `GRAPH_TABLE(...)` SQL operator (23ai) or via the Graph Studio REST API.

4. **Python graph library** — `opg4py` is Oracle's Python client for the graph server.
   On ADB the graph server is embedded; authenticate with DB credentials + JDBC URL.

---

## Step 1 — Create the property graph DDL

Run this once in GRAPHUSER (Graph Studio notebook or via oracledb):

```sql
CREATE PROPERTY GRAPH IF NOT EXISTS synthea_fraud_graph
  VERTEX TABLES (
    oml_user.syn_patients
      KEY (id)
      LABEL patient
      PROPERTIES (id, gender, dob, city, state, zip),

    -- Providers are embedded in encounters; extract distinct via a view
    graphuser.v_providers
      KEY (provider_id)
      LABEL provider
      PROPERTIES (provider_id, organization_id, encounter_count, total_billed),

    graphuser.v_organizations
      KEY (organization_id)
      LABEL organization
      PROPERTIES (organization_id, encounter_count),

    graphuser.v_payers
      KEY (payer_id)
      LABEL payer
      PROPERTIES (payer_id, coverage_count)
  )
  EDGE TABLES (
    oml_user.syn_encounters
      KEY (id)
      SOURCE KEY (patient)     REFERENCES oml_user.syn_patients (id)
      DESTINATION KEY (provider) REFERENCES graphuser.v_providers (provider_id)
      LABEL had_encounter
      PROPERTIES (id, encounterclass, start, stop,
                  base_encounter_cost, total_claim_cost, payer_coverage),

    oml_user.syn_encounters AS enc_to_org
      KEY (id)
      SOURCE KEY (provider)      REFERENCES graphuser.v_providers (provider_id)
      DESTINATION KEY (organization) REFERENCES graphuser.v_organizations (organization_id)
      LABEL works_at,

    oml_user.syn_payer_transitions
      KEY (patient, payer, start_year)
      SOURCE KEY (patient) REFERENCES oml_user.syn_patients (id)
      DESTINATION KEY (payer) REFERENCES graphuser.v_payers (payer_id)
      LABEL insured_by
      PROPERTIES (start_year, end_year, member_id)
  )
  OPTIONS (AUTO DDL OFF)
;
```

**Before running**, create the supporting views in GRAPHUSER:

```sql
-- v_providers: one row per distinct provider in encounters
CREATE OR REPLACE VIEW graphuser.v_providers AS
SELECT
    e.provider                       AS provider_id,
    e.organization                   AS organization_id,
    COUNT(*)                         AS encounter_count,
    SUM(e.total_claim_cost)          AS total_billed
FROM oml_user.syn_encounters e
GROUP BY e.provider, e.organization;

-- v_organizations
CREATE OR REPLACE VIEW graphuser.v_organizations AS
SELECT
    organization  AS organization_id,
    COUNT(*)      AS encounter_count
FROM oml_user.syn_encounters
GROUP BY organization;

-- v_payers
CREATE OR REPLACE VIEW graphuser.v_payers AS
SELECT
    payer        AS payer_id,
    COUNT(*)     AS coverage_count
FROM oml_user.syn_payer_transitions
GROUP BY payer;
```

---

## Step 2 — Verify the graph

```sql
-- Check graph metadata
SELECT graph_name, status, num_vertices, num_edges
FROM user_property_graphs
WHERE graph_name = 'SYNTHEA_FRAUD_GRAPH';

-- Quick vertex count by label
SELECT label_name, COUNT(*) AS cnt
FROM graph_table(synthea_fraud_graph
    MATCH (v)
    COLUMNS (v.label_name))
GROUP BY label_name;
```

---

## Step 3 — PGQL fraud detection queries

### 3a. Patients visiting many different providers (churning)
```sql
SELECT patient_id, provider_count
FROM graph_table(synthea_fraud_graph
    MATCH (p:patient) -[:had_encounter]-> (pr:provider)
    COLUMNS (p.id AS patient_id, COUNT(DISTINCT pr.provider_id) AS provider_count))
GROUP BY patient_id
HAVING provider_count > 10
ORDER BY provider_count DESC
FETCH FIRST 50 ROWS ONLY;
```

### 3b. Providers sharing many patients (provider ring signal)
```sql
SELECT pr1_id, pr2_id, shared_patients
FROM graph_table(synthea_fraud_graph
    MATCH (pr1:provider) <-[:had_encounter]- (p:patient) -[:had_encounter]-> (pr2:provider)
    WHERE pr1.provider_id < pr2.provider_id
    COLUMNS (pr1.provider_id AS pr1_id,
             pr2.provider_id AS pr2_id,
             COUNT(DISTINCT p.id) AS shared_patients))
HAVING shared_patients > 5
ORDER BY shared_patients DESC
FETCH FIRST 50 ROWS ONLY;
```

### 3c. Providers with unusually high billing (upcoding)
```sql
SELECT provider_id, encounter_count, total_billed, avg_per_encounter
FROM graph_table(synthea_fraud_graph
    MATCH (pr:provider)
    COLUMNS (pr.provider_id,
             pr.encounter_count,
             pr.total_billed,
             pr.total_billed / NULLIF(pr.encounter_count, 0) AS avg_per_encounter))
ORDER BY avg_per_encounter DESC
FETCH FIRST 50 ROWS ONLY;
```

### 3d. Payers with high outstanding balances (payment avoidance)
```sql
SELECT payer_id, coverage_count
FROM graph_table(synthea_fraud_graph
    MATCH (pay:payer)
    COLUMNS (pay.payer_id, pay.coverage_count))
ORDER BY coverage_count DESC
FETCH FIRST 20 ROWS ONLY;
```

### 3e. 3-hop fraud chain (patient → provider → org → payer)
```sql
SELECT patient_id, provider_id, org_id, payer_id
FROM graph_table(synthea_fraud_graph
    MATCH (p:patient) -[:had_encounter]-> (pr:provider)
                                       -[:works_at]-> (org:organization),
          (p:patient) -[:insured_by]-> (pay:payer)
    COLUMNS (p.id AS patient_id,
             pr.provider_id,
             org.organization_id AS org_id,
             pay.payer_id))
FETCH FIRST 100 ROWS ONLY;
```

---

## Step 4 — Graph algorithms (run in Graph Studio notebook)

In a Graph Studio Python notebook, or via `opg4py`:

```python
import opg4py

# Connect (ADB embedded graph server)
graph_server = opg4py.graph_server.attach(
    "https://<adb-hostname>:7007",
    "graphuser", creds['password'],
    "jdbc:oracle:thin:@<dsn>"  # thin JDBC URL from tnsnames or ADB
)
session = graph_server.create_session("fraud-detection")
graph = session.read_graph_by_name("SYNTHEA_FRAUD_GRAPH", "pg_view")

analyst = session.create_analyst()

# PageRank — influential providers / high-risk nodes
pr = analyst.pagerank(graph, tol=0.001, damping=0.85, max_iter=100, norm=False)
graph.write_property(pr, "pagerank")

# Weakly Connected Components — find isolated sub-networks
wcc = analyst.wcc(graph)
graph.write_property(wcc, "component_id")

# Louvain — community/ring detection
louvain = analyst.louvain(graph, max_iter=100)
graph.write_property(louvain, "community")

# Betweenness Centrality — bottleneck providers
bc = analyst.betweenness_centrality(graph, norm=True)
graph.write_property(bc, "betweenness")

# Pull results into pandas
import pandas as pd

pagerank_df = pd.DataFrame([
    {"id": v.get_property("provider_id"), "pagerank": v.get_property("pagerank")}
    for v in graph.get_vertices("provider")
]).sort_values("pagerank", ascending=False)

community_df = pd.DataFrame([
    {"id": v.get_property("provider_id"), "community": v.get_property("community")}
    for v in graph.get_vertices("provider")
]).sort_values("community")

print(pagerank_df.head(20))
print(community_df.groupby("community").size().sort_values(ascending=False).head(20))
```

---

## Step 5 — Write results back to Oracle for reporting

```python
# Save PageRank scores to a results table in GRAPHUSER
with conn.cursor() as cur:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS graphuser.fraud_scores (
            provider_id   VARCHAR2(100) PRIMARY KEY,
            pagerank      NUMBER,
            community     NUMBER,
            betweenness   NUMBER,
            updated_at    DATE DEFAULT SYSDATE
        )
    """)
    conn.commit()

    rows = [
        (v.get_property("provider_id"),
         v.get_property("pagerank"),
         v.get_property("community"),
         v.get_property("betweenness"))
        for v in graph.get_vertices("provider")
    ]
    cur.executemany("""
        MERGE INTO graphuser.fraud_scores t
        USING (SELECT :1 AS pid, :2 AS pr, :3 AS comm, :4 AS bc FROM dual) s
        ON (t.provider_id = s.pid)
        WHEN MATCHED THEN UPDATE SET pagerank=s.pr, community=s.comm, betweenness=s.bc, updated_at=SYSDATE
        WHEN NOT MATCHED THEN INSERT (provider_id, pagerank, community, betweenness)
             VALUES (s.pid, s.pr, s.comm, s.bc)
    """, rows)
    conn.commit()
```

---

## Fraud pattern checklist

| Pattern | Detection method | Query / algorithm |
|---|---|---|
| Provider rings (coordinated billing) | Shared patients between pairs of providers | Query 3b + Louvain communities |
| Patient churning | Patient visits many providers in short window | Query 3a |
| Upcoding | Provider avg cost >> peers | Query 3c |
| Phantom billing | Claims without matching encounters | Join syn_claims ↔ syn_encounters |
| Hub providers | High PageRank + high betweenness | PageRank + Betweenness algorithms |
| Isolated fraud networks | Small disconnected sub-graphs | WCC (Weakly Connected Components) |

---

## Workflow for each session

1. Connect as GRAPHUSER using `GRAPHUSER_SECRET_OCID`
2. If graph doesn't exist → run Step 1 DDL (views first, then CREATE PROPERTY GRAPH)
3. Verify with Step 2 queries
4. Run PGQL pattern queries (Step 3) to find specific fraud patterns
5. Run graph algorithms (Step 4) for scoring and community detection
6. Write scores to `graphuser.fraud_scores` (Step 5)
7. Plot results using `tools/plot.py` or Plotly directly

**Always use Oracle SQL conventions:** `FETCH FIRST n ROWS ONLY` not `LIMIT`,
`SYSDATE` not `NOW()`, `MERGE INTO` for upserts.
