"""
Create T_PROVIDERS, T_ORGANIZATIONS, T_PAYERS tables in GRAPHUSER.
Views cannot be used as property graph element tables (ORA-42449),
so we materialise them as real tables.
Run as GRAPHUSER.
"""
import oci, json, base64, os, oracledb
from dotenv import load_dotenv

load_dotenv('/mnt/c/Git_Repos/oci-ai-playground/.env')
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']

config = oci.config.from_file()
secrets_client = oci.secrets.SecretsClient(config)
bundle = secrets_client.get_secret_bundle(os.environ['GRAPHUSER_SECRET_OCID'])
creds = json.loads(base64.b64decode(bundle.data.secret_bundle_content.content).decode())

conn = oracledb.connect(user=creds['user_name'], password=creds['password'], dsn=creds['dsn'])
print(f"Connected as {creds['user_name']}")

ddls = [
    ("T_PROVIDERS", """
        CREATE TABLE t_providers AS
        SELECT
            e.provider                         AS provider_id,
            e.organization                     AS organization_id,
            COUNT(*)                           AS encounter_count,
            ROUND(SUM(e.total_claim_cost), 2)  AS total_billed,
            ROUND(AVG(e.total_claim_cost), 2)  AS avg_per_encounter
        FROM oml_user.syn_encounters e
        GROUP BY e.provider, e.organization
    """, "ADD CONSTRAINT pk_t_providers PRIMARY KEY (provider_id)"),
    ("T_ORGANIZATIONS", """
        CREATE TABLE t_organizations AS
        SELECT
            organization  AS organization_id,
            COUNT(*)      AS encounter_count
        FROM oml_user.syn_encounters
        GROUP BY organization
    """, "ADD CONSTRAINT pk_t_organizations PRIMARY KEY (organization_id)"),
    ("T_PAYERS", """
        CREATE TABLE t_payers AS
        SELECT
            payer           AS payer_id,
            COUNT(*)        AS coverage_count,
            MIN(start_date) AS first_seen,
            MAX(end_date)   AS last_seen
        FROM oml_user.syn_payer_transitions
        GROUP BY payer
    """, "ADD CONSTRAINT pk_t_payers PRIMARY KEY (payer_id)"),
]

with conn.cursor() as cur:
    for table_name, create_sql, pk_ddl in ddls:
        try:
            cur.execute(f"DROP TABLE {table_name} PURGE")
            print(f"  Dropped existing {table_name}")
        except Exception:
            pass
        print(f"  Creating {table_name}...")
        cur.execute(create_sql)
        conn.commit()
        cur.execute(f"ALTER TABLE {table_name} {pk_ddl}")
        conn.commit()
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        print(f"  {table_name}: {cur.fetchone()[0]:,} rows, PK added")

print("\nEntity tables ready.")
conn.close()
