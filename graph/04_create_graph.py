"""
Create the synthea_fraud_graph property graph in GRAPHUSER.
Prerequisite: run 01, 02, 03 first.
Run as GRAPHUSER.

Graph topology:
  Vertices: patient, provider, organization, payer
  Edges:
    had_encounter  — patient  -> provider      (syn_encounters)
    works_at       — provider -> organization  (syn_encounters)
    insured_by     — patient  -> payer         (syn_payer_transitions)

Actual column names (verified from OML_USER):
  syn_patients:         ID, BIRTHDATE, GENDER, CITY, STATE, ZIP, ...
  syn_encounters:       ID, START_TS, STOP_TS, PATIENT, ORGANIZATION, PROVIDER,
                        PAYER, ENCOUNTER_CLASS, BASE_ENCOUNTER_COST, TOTAL_CLAIM_COST
  syn_payer_transitions: PATIENT, PAYER, START_DATE, END_DATE, MEMBERID, ...
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

create_graph = """CREATE PROPERTY GRAPH synthea_fraud_graph
  VERTEX TABLES (
    syn_patients
      KEY (id)
      LABEL patient
      PROPERTIES ARE ALL COLUMNS,

    t_providers
      KEY (provider_id)
      LABEL provider
      PROPERTIES ARE ALL COLUMNS,

    t_organizations
      KEY (organization_id)
      LABEL organization
      PROPERTIES ARE ALL COLUMNS,

    t_payers
      KEY (payer_id)
      LABEL payer
      PROPERTIES ARE ALL COLUMNS
  )
  EDGE TABLES (
    syn_encounters AS patient_to_provider
      KEY (id)
      SOURCE KEY (patient)       REFERENCES syn_patients (id)
      DESTINATION KEY (provider) REFERENCES t_providers (provider_id)
      LABEL had_encounter
      PROPERTIES ARE ALL COLUMNS,

    syn_encounters AS provider_to_org
      KEY (id)
      SOURCE KEY (provider)          REFERENCES t_providers (provider_id)
      DESTINATION KEY (organization) REFERENCES t_organizations (organization_id)
      LABEL works_at,

    syn_payer_transitions AS patient_to_payer
      KEY (patient, payer, start_date)
      SOURCE KEY (patient) REFERENCES syn_patients (id)
      DESTINATION KEY (payer) REFERENCES t_payers (payer_id)
      LABEL insured_by
      PROPERTIES ARE ALL COLUMNS
  )"""

with conn.cursor() as cur:
    try:
        cur.execute("DROP PROPERTY GRAPH synthea_fraud_graph")
        conn.commit()
        print("Dropped existing synthea_fraud_graph")
    except Exception:
        pass

    print("Creating synthea_fraud_graph...")
    cur.execute(create_graph)
    conn.commit()
    print("Graph created.")

    cur.execute("""
        SELECT graph_name, owner FROM all_property_graphs
        WHERE graph_name = 'SYNTHEA_FRAUD_GRAPH'
    """)
    row = cur.fetchone()
    print(f"Verified: {row[1]}.{row[0]}" if row else "WARNING: not found in all_property_graphs")

conn.close()
