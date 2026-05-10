"""
Fraud detection SQL/PGQL queries against synthea_fraud_graph.
Uses Oracle 23ai GRAPH_TABLE() operator — no separate graph server needed.

Rule: GRAPH_TABLE COLUMNS must be scalar — aggregations go in the outer SELECT.

Patterns detected:
  1. Patient churning       — patients visiting many distinct providers
  2. Provider rings         — provider pairs sharing many patients
  3. Upcoding               — providers with abnormally high avg billing
  4. High-volume providers  — providers with most encounters
  5. Payer exposure         — payers with most insured patients
  6. 3-hop fraud chain      — patient -> provider -> org + patient -> payer
"""
import oci, json, base64, os, oracledb, pandas as pd
from dotenv import load_dotenv

load_dotenv('/mnt/c/Git_Repos/oci-ai-playground/.env')
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']

config = oci.config.from_file()
secrets_client = oci.secrets.SecretsClient(config)
bundle = secrets_client.get_secret_bundle(os.environ['GRAPHUSER_SECRET_OCID'])
creds = json.loads(base64.b64decode(bundle.data.secret_bundle_content.content).decode())

conn = oracledb.connect(user=creds['user_name'], password=creds['password'], dsn=creds['dsn'])
print(f"Connected as {creds['user_name']}\n")


def run(label, sql, top=20):
    print(f"{'─'*60}")
    print(f"  {label}")
    print(f"{'─'*60}")
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(top)
    df = pd.DataFrame(rows, columns=cols)
    print(df.to_string(index=False))
    print()
    return df


# ── 1. Patient churning ────────────────────────────────────────────────────────
df1 = run("Patient churning — patients visiting many distinct providers", """
    SELECT patient_id,
           COUNT(DISTINCT provider_id)     AS provider_count,
           ROUND(SUM(total_claim_cost), 2) AS total_spent
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (p IS patient) -[e IS had_encounter]-> (pr IS provider)
        COLUMNS (
            p.id                AS patient_id,
            pr.provider_id      AS provider_id,
            e.total_claim_cost  AS total_claim_cost
        )
    )
    GROUP BY patient_id
    HAVING COUNT(DISTINCT provider_id) > 8
    ORDER BY provider_count DESC
    FETCH FIRST 20 ROWS ONLY
""")

# ── 2. Provider rings — pairs sharing many patients ────────────────────────────
df2 = run("Provider rings — pairs sharing many patients", """
    SELECT pr1_id, pr2_id,
           COUNT(DISTINCT patient_id) AS shared_patients
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pr1 IS provider) <-[e1 IS had_encounter]- (p IS patient)
                                -[e2 IS had_encounter]-> (pr2 IS provider)
        WHERE pr1.provider_id < pr2.provider_id
        COLUMNS (
            pr1.provider_id  AS pr1_id,
            pr2.provider_id  AS pr2_id,
            p.id             AS patient_id
        )
    )
    GROUP BY pr1_id, pr2_id
    HAVING COUNT(DISTINCT patient_id) > 5
    ORDER BY shared_patients DESC
    FETCH FIRST 20 ROWS ONLY
""")

# ── 3. Upcoding — providers with high avg billing ─────────────────────────────
df3 = run("Upcoding signal — providers with highest avg cost per encounter (min 100 encounters)", """
    SELECT provider_id, encounter_count,
           ROUND(total_billed, 2)       AS total_billed,
           ROUND(avg_per_encounter, 2)  AS avg_per_encounter
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pr IS provider)
        COLUMNS (
            pr.provider_id        AS provider_id,
            pr.encounter_count    AS encounter_count,
            pr.total_billed       AS total_billed,
            pr.avg_per_encounter  AS avg_per_encounter
        )
    )
    WHERE encounter_count >= 100
    ORDER BY avg_per_encounter DESC
    FETCH FIRST 20 ROWS ONLY
""")

# ── 4. High-volume providers ───────────────────────────────────────────────────
df4 = run("High-volume providers — most encounters", """
    SELECT provider_id, encounter_count,
           ROUND(total_billed, 2) AS total_billed
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pr IS provider)
        COLUMNS (
            pr.provider_id      AS provider_id,
            pr.encounter_count  AS encounter_count,
            pr.total_billed     AS total_billed
        )
    )
    ORDER BY encounter_count DESC
    FETCH FIRST 20 ROWS ONLY
""")

# ── 5. Payer exposure ──────────────────────────────────────────────────────────
df5 = run("Payer exposure — most unique patients insured", """
    SELECT payer_id,
           COUNT(DISTINCT patient_id) AS unique_patients
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (pay IS payer) <-[i IS insured_by]- (p IS patient)
        COLUMNS (
            pay.payer_id  AS payer_id,
            p.id          AS patient_id
        )
    )
    GROUP BY payer_id
    ORDER BY unique_patients DESC
    FETCH FIRST 20 ROWS ONLY
""")

# ── 6. 3-hop chain: patient -> provider -> org, patient -> payer ───────────────
df6 = run("3-hop chains — patient/provider/org/payer (sample)", """
    SELECT patient_id, provider_id, org_id, payer_id
    FROM GRAPH_TABLE(synthea_fraud_graph
        MATCH (p IS patient) -[e IS had_encounter]-> (pr IS provider)
                             -[w IS works_at]->      (org IS organization),
              (p IS patient) -[i IS insured_by]->    (pay IS payer)
        COLUMNS (
            p.id                AS patient_id,
            pr.provider_id      AS provider_id,
            org.organization_id AS org_id,
            pay.payer_id        AS payer_id
        )
    )
    FETCH FIRST 20 ROWS ONLY
""")

conn.close()
print("Done.")
