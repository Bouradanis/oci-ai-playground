import os
import json
import base64
from pathlib import Path

import oci
import oracledb
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']

_connection: oracledb.Connection | None = None


def _connect() -> oracledb.Connection:
    config = oci.config.from_file()
    secrets_client = oci.secrets.SecretsClient(config)
    secret_bundle = secrets_client.get_secret_bundle(os.environ['OML_USER_CREDS_SECRET_OCID'])
    creds = json.loads(
        base64.b64decode(secret_bundle.data.secret_bundle_content.content).decode('utf-8')
    )
    return oracledb.connect(
        user=creds['user_name'],
        password=creds['password'],
        dsn=creds['dsn'],
    )


def get_connection() -> oracledb.Connection:
    global _connection
    if _connection is None:
        _connection = _connect()
    return _connection
