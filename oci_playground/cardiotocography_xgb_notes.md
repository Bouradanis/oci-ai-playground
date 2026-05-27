# Cardiotocography XGBoost — What We Did & Why

## Architecture decisions

### Schema separation
- **Schema A (`OML_USER`)** — owns all business data including `CARDIOTOCOGRAPHY` table
- **Schema B (`CARDIO_MODEL_USER`)** — owns the trained ML model only
- Schema A scores using `PREDICTION(CARDIO_MODEL_USER.XGB_CARDIO_CLASS USING ...)` — cross-schema, in-database, no network hop

### Why ONNX instead of pickle?
- Pickle requires Python + same env to score → can't call from SQL
- ONNX is a standardised binary format Oracle's C++ runtime understands
- Once imported via `DBMS_DATA_MINING.IMPORT_SERMODEL`, the model is a native Oracle object
- Scoring happens inside the DB process via SQL `PREDICTION()` — microsecond latency

### Why not `oml` Python package?
- `oml` (OML4Py) is Oracle-provided, not on PyPI — only available inside ADB's managed Python env
- We use `oracledb` + raw PLSQL calls (`DBMS_DATA_MINING`) to achieve the same result
- This works from any external Python environment (local conda, CI, etc.)

### Why Optuna for hyperparameter search?
- Optuna uses TPE (Tree-structured Parzen Estimator) — a Bayesian optimisation method
- More efficient than grid search: focuses trials on promising regions of the search space
- 50 trials with 5-fold CV gives a good accuracy/time trade-off on 2126 rows

---

## Prerequisites

### Python packages (conda env: `olist_mcp`)
```bash
pip install xgboost optuna scikit-learn onnxmltools skl2onnx onnx
```

### `.env` additions needed
```
ADMIN_SECRET_OCID=ocid1.vaultsecret.oc1...   # OCI Vault secret with ADMIN credentials
```

The ADMIN secret should be a JSON object:
```json
{"user_name": "ADMIN", "password": "...", "dsn": "oracletestdb_high"}
```

---

## Step-by-step summary

| Step | What happens | Where |
|------|-------------|-------|
| 1 | Connect to ADB as OML_USER | Local Python |
| 2 | `SELECT * FROM CARDIOTOCOGRAPHY` → pandas DataFrame | DB → Local |
| 3 | PowerTransformer (Yeo-Johnson) + StandardScaler | Local |
| 4 | Optuna 50-trial Bayesian search, 5-fold CV | Local |
| 5 | Train final XGBClassifier with best params | Local |
| 6 | `onnxmltools.convert_xgboost()` → ONNX bytes | Local |
| 7 | Connect as ADMIN, create `CARDIO_MODEL_USER`, grant `CREATE MINING MODEL` | DB |
| 8 | Connect as `CARDIO_MODEL_USER`, call `DBMS_DATA_MINING.IMPORT_SERMODEL` | DB |
| 9 | Grant `SELECT` on model to `OML_USER` via `DBMS_DATA_MINING.GRANT_MODEL_PRIVILEGE` | DB |
| 10 | Score from OML_USER: `SELECT PREDICTION(CARDIO_MODEL_USER.XGB_CARDIO_CLASS USING ...) FROM CARDIOTOCOGRAPHY` | In-DB |

---

## Key Oracle PLSQL calls

### Drop existing model
```sql
BEGIN DBMS_DATA_MINING.DROP_MODEL('XGB_CARDIO_CLASS'); END;
```

### Import ONNX model
```sql
BEGIN DBMS_DATA_MINING.IMPORT_SERMODEL(:onnx_blob, 'XGB_CARDIO_CLASS'); END;
```

### Grant scoring privilege cross-schema
```sql
BEGIN
    DBMS_DATA_MINING.GRANT_MODEL_PRIVILEGE(
        privilege  => 'SELECT',
        model_name => 'CARDIO_MODEL_USER.XGB_CARDIO_CLASS',
        user_name  => 'OML_USER'
    );
END;
```

### Score in SQL (from OML_USER)
```sql
SELECT
    CLASS AS true_class,
    PREDICTION(CARDIO_MODEL_USER.XGB_CARDIO_CLASS
        USING LB, AC, FM, UC, DL, DS, DP,
              ASTV, MSTV, ALTV, MLTV,
              WIDTH, MIN, MAX, NMAX, NZEROS,
              MODE_VALUE, MEAN, MEDIAN, VARIANCE, TENDENCY
    ) AS predicted_class
FROM CARDIOTOCOGRAPHY;
```

### Check model registry
```sql
SELECT model_name, algorithm, mining_function FROM user_mining_models;
```

---

## Extending to a new model (future reference)

1. Create a new schema (e.g., `FRAUD_MODEL_USER`) with same grants
2. Train your model in a separate notebook
3. Convert to ONNX, import to new schema
4. Grant `SELECT` to the schema that needs to score
5. Score via `PREDICTION(FRAUD_MODEL_USER.model_name USING ...)`

Each model lives in isolation — no dependency conflicts between schemas.
