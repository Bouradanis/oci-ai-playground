"""Delivery-delay prediction logic for the /predict endpoint.

Thin wrapper around olist_copilot/tools/predict.py — no model/preprocessing
logic lives here, per this repo's division of labor (ML engineer owns the
model, frontend developer only wraps and displays it).

FEATURE_COLS has 14 entries, but the Page 5 form only collects 12 of them
directly (see main.py's PredictRequest) -- SAME_STATE_FLAG and MAX_DISTANCE_KM
are derived here from PRIMARY_SELLER_STATE/CUSTOMER_STATE rather than asked of
the user a second time:
  - SAME_STATE_FLAG is trivially `seller_state == customer_state`, exactly
    mirroring DELIVERY_DELAY_FEATURES' own CASE WHEN derivation (see
    databases/OML_USER/tables/delivery_delay_features.sql).
  - MAX_DISTANCE_KM was computed at training time from precise zip-prefix
    centroids, which the interactive form has no access to (it only collects
    state). BRAZIL_STATE_COORDS (state-capital-ish centroids, defined in the
    ML engineer's olist_copilot/tools/predict.py alongside a docstring saying
    this is meant to "match what the model itself actually uses" at this
    granularity) is used as the best available stand-in: the same Haversine
    formula the feature table itself uses, applied to state centroids instead
    of zip centroids.
"""
import math
import sys
from pathlib import Path

_OLIST_COPILOT_DIR = Path(__file__).parent.parent / "olist_copilot"
if str(_OLIST_COPILOT_DIR) not in sys.path:
    sys.path.insert(0, str(_OLIST_COPILOT_DIR))

from tools.predict import (  # noqa: E402  (path insert must run first)
    FEATURE_COLS,
    BRAZIL_STATE_COORDS,
    get_dropdown_options,
    predict_delivery_delay,
)


def get_form_options() -> dict:
    """Dropdown options for the Page 5 form, straight from the ML engineer's module."""
    return get_dropdown_options()


def _haversine_km(lat1, lon1, lat2, lon2):
    """Same Haversine formula as delivery_delay_features.sql's item_distance CTE,
    just applied to state centroids instead of zip-prefix centroids."""
    r = 6371
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def predict(features: dict) -> dict:
    """features: dict with the 12 form-collected FEATURE_COLS (everything except
    SAME_STATE_FLAG and MAX_DISTANCE_KM, which are derived here -- see module
    docstring). Returns predicted delay (days) plus the seller/customer state
    coordinates so the APEX page can render a route without needing its own
    geocoding."""
    seller_state = features["PRIMARY_SELLER_STATE"]
    customer_state = features["CUSTOMER_STATE"]
    seller_coords = BRAZIL_STATE_COORDS.get(seller_state)
    customer_coords = BRAZIL_STATE_COORDS.get(customer_state)

    full_features = dict(features)
    full_features["SAME_STATE_FLAG"] = 1 if seller_state == customer_state else 0
    full_features["MAX_DISTANCE_KM"] = (
        _haversine_km(*seller_coords, *customer_coords)
        if seller_coords and customer_coords
        else None
    )

    missing = [c for c in FEATURE_COLS if c not in full_features]
    if missing:
        raise ValueError(f"Missing required feature(s): {', '.join(missing)}")

    predicted_days = predict_delivery_delay(full_features)

    return {
        "predicted_delay_days": round(predicted_days, 2),
        "seller_state": seller_state,
        "seller_lat": seller_coords[0] if seller_coords else None,
        "seller_lon": seller_coords[1] if seller_coords else None,
        "customer_state": customer_state,
        "customer_lat": customer_coords[0] if customer_coords else None,
        "customer_lon": customer_coords[1] if customer_coords else None,
    }