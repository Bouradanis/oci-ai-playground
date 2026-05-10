"""
Standalone loader for syn_claims and syn_claims_transactions.
Uses oracledb + pandas (no Spark) — reliable for large tables over a slow JDBC link.
Run with: /home/abourantanis/miniconda3/envs/olist_mcp/bin/python load_claims.py
"""
import oci, json, base64, os, oracledb, pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv('/mnt/c/Git_Repos/oci-ai-playground/.env')
oracledb.defaults.config_dir = os.environ['TNS_ADMIN']

config         = oci.config.from_file()
secrets_client = oci.secrets.SecretsClient(config)
bundle         = secrets_client.get_secret_bundle(os.environ['OML_USER_CREDS_SECRET_OCID'])
creds          = json.loads(base64.b64decode(bundle.data.secret_bundle_content.content).decode())

DATA_DIR   = '/mnt/c/Git_Repos/oci-ai-playground/output/csv'
BATCH_SIZE = 50_000
CHUNK_SIZE = 500_000
TS_FMT     = '%Y-%m-%dT%H:%M:%SZ'


def load_table(conn, table, csv_name, col_map, ts_cols, num_cols, total_hint):
    """
    col_map  : {oracle_col: csv_col}  — defines column order for INSERT
    ts_cols  : set of CSV column names that are ISO timestamps → parsed to date
    num_cols : set of CSV column names that are numeric → read as float
    """
    csv_cols    = list(col_map.values())   # CSV column names, in INSERT order
    oracle_cols = list(col_map.keys())     # Oracle column names, in INSERT order

    print(f'\n{"─"*60}')
    print(f'Loading {table}  (est. {total_hint:,} rows)')

    with conn.cursor() as cur:
        cur.execute(f'TRUNCATE TABLE {table}')
    conn.commit()
    print(f'  Truncated {table}')

    placeholders = ', '.join([f':{i+1}' for i in range(len(oracle_cols))])
    sql = f'INSERT INTO {table} ({", ".join(oracle_cols)}) VALUES ({placeholders})'

    # dtype map: explicit for every column — prevents pandas mixed-type inference
    dtype_map = {col: (float if col in num_cols else str) for col in csv_cols}

    total = 0
    t0    = datetime.now()

    for chunk in pd.read_csv(
        f'{DATA_DIR}/{csv_name}.csv',
        usecols=csv_cols,          # only load needed columns
        dtype=dtype_map,           # numerics as float, rest as object/str
        keep_default_na=False,
        na_values=[''],
        chunksize=CHUNK_SIZE,
    ):
        # Reorder columns to match INSERT statement
        chunk = chunk[csv_cols]

        # Parse ISO timestamp columns → Python date (or None for NaT)
        for col in ts_cols:
            chunk[col] = pd.to_datetime(chunk[col], format=TS_FMT, errors='coerce').dt.date

        # Convert all NaN / NaT → None so oracledb sends NULL
        chunk = chunk.astype(object).where(pd.notnull(chunk), None)

        rows = list(chunk.itertuples(index=False, name=None))

        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            with conn.cursor() as cur:
                cur.executemany(sql, batch)
            conn.commit()
            total += len(batch)
            elapsed = max((datetime.now() - t0).seconds, 1)
            print(f'  {total:>10,} rows  |  {total/elapsed:,.0f} rows/s  |  {elapsed}s elapsed')

    elapsed = max((datetime.now() - t0).seconds, 1)
    print(f'  DONE: {total:,} rows in {elapsed}s  ({total/elapsed:,.0f} rows/s)')


# ── SYN_CLAIMS ────────────────────────────────────────────────────────────────
CLAIMS_COLS = {
    'id':                             'Id',
    'patient_id':                     'PATIENTID',
    'provider_id':                    'PROVIDERID',
    'primary_patient_insurance_id':   'PRIMARYPATIENTINSURANCEID',
    'secondary_patient_insurance_id': 'SECONDARYPATIENTINSURANCEID',
    'department_id':                  'DEPARTMENTID',
    'patient_department_id':          'PATIENTDEPARTMENTID',
    'diagnosis1':                     'DIAGNOSIS1',
    'diagnosis2':                     'DIAGNOSIS2',
    'diagnosis3':                     'DIAGNOSIS3',
    'diagnosis4':                     'DIAGNOSIS4',
    'diagnosis5':                     'DIAGNOSIS5',
    'diagnosis6':                     'DIAGNOSIS6',
    'diagnosis7':                     'DIAGNOSIS7',
    'diagnosis8':                     'DIAGNOSIS8',
    'referring_provider_id':          'REFERRINGPROVIDERID',
    'appointment_id':                 'APPOINTMENTID',
    'current_illness_date':           'CURRENTILLNESSDATE',
    'service_date':                   'SERVICEDATE',
    'supervising_provider_id':        'SUPERVISINGPROVIDERID',
    'status1':                        'STATUS1',
    'status2':                        'STATUS2',
    'statusp':                        'STATUSP',
    'outstanding1':                   'OUTSTANDING1',
    'outstanding2':                   'OUTSTANDING2',
    'outstandingp':                   'OUTSTANDINGP',
    'last_billed_date1':              'LASTBILLEDDATE1',
    'last_billed_date2':              'LASTBILLEDDATE2',
    'last_billed_datep':              'LASTBILLEDDATEP',
    'healthcare_claim_type_id1':      'HEALTHCARECLAIMTYPEID1',
    'healthcare_claim_type_id2':      'HEALTHCARECLAIMTYPEID2',
}
CLAIMS_TS  = {'CURRENTILLNESSDATE','SERVICEDATE','LASTBILLEDDATE1','LASTBILLEDDATE2','LASTBILLEDDATEP'}
CLAIMS_NUM = {'DEPARTMENTID','PATIENTDEPARTMENTID','OUTSTANDING1','OUTSTANDING2','OUTSTANDINGP',
              'HEALTHCARECLAIMTYPEID1','HEALTHCARECLAIMTYPEID2'}

# ── SYN_CLAIMS_TRANSACTIONS ───────────────────────────────────────────────────
CLAIMSTXN_COLS = {
    'id':                      'ID',
    'claim_id':                'CLAIMID',
    'charge_id':               'CHARGEID',
    'patient_id':              'PATIENTID',
    'txn_type':                'TYPE',
    'amount':                  'AMOUNT',
    'method':                  'METHOD',
    'from_date':               'FROMDATE',
    'to_date':                 'TODATE',
    'place_of_service':        'PLACEOFSERVICE',
    'procedure_code':          'PROCEDURECODE',
    'modifier1':               'MODIFIER1',
    'modifier2':               'MODIFIER2',
    'diagnosis_ref1':          'DIAGNOSISREF1',
    'diagnosis_ref2':          'DIAGNOSISREF2',
    'diagnosis_ref3':          'DIAGNOSISREF3',
    'diagnosis_ref4':          'DIAGNOSISREF4',
    'units':                   'UNITS',
    'department_id':           'DEPARTMENTID',
    'notes':                   'NOTES',
    'unit_amount':             'UNITAMOUNT',
    'transfer_out_id':         'TRANSFEROUTID',
    'transfer_type':           'TRANSFERTYPE',
    'payments':                'PAYMENTS',
    'adjustments':             'ADJUSTMENTS',
    'transfers':               'TRANSFERS',
    'outstanding':             'OUTSTANDING',
    'appointment_id':          'APPOINTMENTID',
    'line_note':               'LINENOTE',
    'patient_insurance_id':    'PATIENTINSURANCEID',
    'fee_schedule_id':         'FEESCHEDULEID',
    'provider_id':             'PROVIDERID',
    'supervising_provider_id': 'SUPERVISINGPROVIDERID',
}
CLAIMSTXN_TS  = {'FROMDATE', 'TODATE'}
CLAIMSTXN_NUM = {'CHARGEID','AMOUNT','DIAGNOSISREF1','DIAGNOSISREF2','DIAGNOSISREF3','DIAGNOSISREF4',
                 'UNITS','DEPARTMENTID','UNITAMOUNT','TRANSFEROUTID',
                 'PAYMENTS','ADJUSTMENTS','TRANSFERS','OUTSTANDING','FEESCHEDULEID'}


# ── main ──────────────────────────────────────────────────────────────────────
print(f'Connecting as {creds["user_name"]}...')
conn = oracledb.connect(user=creds['user_name'], password=creds['password'], dsn=creds['dsn'])
print('Connected.')

load_table(conn, 'syn_claims_transactions', 'claims_transactions', CLAIMSTXN_COLS, CLAIMSTXN_TS, CLAIMSTXN_NUM, 21_345_339)

print('\n── Final counts ──')
with conn.cursor() as cur:
    for t in ['syn_claims', 'syn_claims_transactions']:
        cur.execute(f'SELECT COUNT(*) FROM {t}')
        print(f'  {t:<35} {cur.fetchone()[0]:>12,}')

conn.close()
print('\nDone.')
