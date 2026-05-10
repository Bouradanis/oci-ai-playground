"""
Grant SELECT on all Synthea (OML_USER) tables to GRAPHUSER.
Run as ADMIN.
"""
import oci, json, base64, os, oracledb
from dotenv import load_dotenv

load_dotenv('/mnt/c/Git_Repos/oci-ai-playground/.env')
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']

config = oci.config.from_file()
secrets_client = oci.secrets.SecretsClient(config)
bundle = secrets_client.get_secret_bundle(os.environ['ADMIN_SECRET_OCID'])
creds = json.loads(base64.b64decode(bundle.data.secret_bundle_content.content).decode())

conn = oracledb.connect(user=creds['user_name'], password=creds['password'], dsn=creds['dsn'])
print(f"Connected as {creds['user_name']}")

synthea_tables = [
    'SYN_PATIENTS', 'SYN_ENCOUNTERS', 'SYN_PAYER_TRANSITIONS',
    'SYN_CONDITIONS', 'SYN_MEDICATIONS', 'SYN_PROCEDURES',
    'SYN_OBSERVATIONS', 'SYN_CLAIMS', 'SYN_CLAIMS_TRANSACTIONS',
    'SYN_IMMUNIZATIONS', 'SYN_ALLERGIES', 'SYN_CAREPLANS', 'SYN_DEVICES',
]

with conn.cursor() as cur:
    for table in synthea_tables:
        cur.execute(f"GRANT SELECT ON OML_USER.{table} TO GRAPHUSER")
        print(f"  GRANTED: {table}")
    conn.commit()

print("\nAll grants applied.")
conn.close()
