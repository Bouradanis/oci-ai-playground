from pathlib import Path

import joblib

from db.connection import get_connection

MODEL_NAME = "DELIVERY_DELAY_GLM_FINAL"

# Checkout-time framing: every one of these must be genuinely knowable at purchase
# time -- see databases/OML_USER/tables/delivery_delay_features.sql for the full
# rationale and the VIF-driven column choices (ml/delivery_delay/ notebooks).
FEATURE_COLS = [
    "PROMISED_DELIVERY_DAYS", "NUM_ITEMS", "TOTAL_PRICE", "TOTAL_FREIGHT_VALUE",
    "TOTAL_WEIGHT_G", "PRIMARY_PRODUCT_CATEGORY_NAME_ENGLISH", "PRIMARY_SELLER_STATE",
    "HEAVIEST_PRODUCT_WEIGHT_G", "HEAVIEST_PRODUCT_LENGTH_CM", "HEAVIEST_PRODUCT_HEIGHT_CM",
    "HEAVIEST_PRODUCT_WIDTH_CM", "CUSTOMER_STATE", "SAME_STATE_FLAG", "MAX_DISTANCE_KM",
]

_TRANSFORMER_PATH = Path(__file__).parent.parent / "ml" / "delivery_delay" / "yeo_johnson_target_transformer.joblib"
_transformer = None


def _get_transformer():
    global _transformer
    if _transformer is None:
        _transformer = joblib.load(_TRANSFORMER_PATH)
    return _transformer


def get_dropdown_options() -> dict:
    """Distinct values for the categorical fields, for populating form dropdowns."""
    conn = get_connection()
    options = {}
    with conn.cursor() as cur:
        for col in ("PRIMARY_PRODUCT_CATEGORY_NAME_ENGLISH", "PRIMARY_SELLER_STATE", "CUSTOMER_STATE"):
            cur.execute(f"SELECT DISTINCT {col} FROM DELIVERY_DELAY_FEATURES WHERE {col} IS NOT NULL ORDER BY 1")
            options[col] = [r[0] for r in cur.fetchall()]
    return options


def predict_delivery_delay(features: dict) -> float:
    """features: dict mapping FEATURE_COLS names -> values. Returns predicted delay in real days
    (positive = late, negative = early), inverse-transformed from the model's Yeo-Johnson-scale output."""
    using_clause = ", ".join(f":{i + 1} AS {col}" for i, col in enumerate(FEATURE_COLS))
    sql = f"SELECT PREDICTION({MODEL_NAME} USING {using_clause}) AS PRED FROM DUAL"
    bind_values = [features[col] for col in FEATURE_COLS]

    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(sql, bind_values)
        pred_transformed = cur.fetchone()[0]

    pt = _get_transformer()
    pred_days = pt.inverse_transform([[pred_transformed]])[0][0]
    return float(pred_days)