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

# Approximate Brazilian state capital coordinates (lat, lon) -- static reference data,
# used only to illustrate the seller->customer route on a map. State-level, not a
# precise address-to-address distance (the form only collects state, not an exact
# location -- matches what the model itself actually uses).
BRAZIL_STATE_COORDS = {
    "AC": (-9.97, -67.81), "AL": (-9.65, -35.73), "AP": (0.03, -51.07), "AM": (-3.12, -60.02),
    "BA": (-12.97, -38.51), "CE": (-3.72, -38.54), "DF": (-15.78, -47.93), "ES": (-20.32, -40.34),
    "GO": (-16.68, -49.25), "MA": (-2.53, -44.30), "MT": (-15.60, -56.10), "MS": (-20.44, -54.65),
    "MG": (-19.92, -43.94), "PA": (-1.46, -48.50), "PB": (-7.12, -34.86), "PR": (-25.43, -49.27),
    "PE": (-8.05, -34.90), "PI": (-5.09, -42.80), "RJ": (-22.91, -43.17), "RN": (-5.79, -35.21),
    "RS": (-30.03, -51.23), "RO": (-8.76, -63.90), "RR": (2.82, -60.67), "SC": (-27.60, -48.55),
    "SP": (-23.55, -46.63), "SE": (-10.91, -37.07), "TO": (-10.18, -48.33),
}

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