"""
Create synonyms in GRAPHUSER pointing to OML_USER Synthea tables.
Property graph DDL cannot use cross-schema table references but can use synonyms.
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

synonyms = [
    ('SYN_PATIENTS',          'OML_USER.SYN_PATIENTS'),
    ('SYN_ENCOUNTERS',        'OML_USER.SYN_ENCOUNTERS'),
    ('SYN_PAYER_TRANSITIONS', 'OML_USER.SYN_PAYER_TRANSITIONS'),
]

with conn.cursor() as cur:
    for syn_name, target in synonyms:
        try:
            cur.execute(f"DROP SYNONYM {syn_name}")
        except Exception:
            pass
        cur.execute(f"CREATE SYNONYM {syn_name} FOR {target}")
        cur.execute(f"SELECT COUNT(*) FROM {syn_name}")
        cnt = cur.fetchone()[0]
        print(f"  {syn_name} -> {target}  ({cnt:,} rows)")

conn.commit()
print("\nSynonyms ready.")
conn.close()
