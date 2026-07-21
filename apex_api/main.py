"""FastAPI backend for the Oracle APEX front-end (Phase 5).

Per-page dedicated endpoints (not a single generic /chat with intent
classification — see KAN-6 design decision). This module implements:
  - /query (Chat page, Page 2): natural-language question -> Claude
    generates SQL -> executed against the ADB -> JSON result.
  - /predict/options and /predict (Delivery Estimate page, Page 5): wraps
    the ML engineer's delivery-delay GLM model (olist_copilot/tools/predict.py)
    via apex_api/predict.py.

Every endpoint is gated by trusted_auth.verify_apex_request, which
independently verifies an HMAC signature APEX attaches to the request
(see trusted_auth.py's module docstring for the full rationale). This
mirrors the existing Streamlit app's principle that admin-only actions must
be blocked server-side, not just hidden in the UI — here it's stronger:
every request's asserted identity is cryptographically checked, not just
role-gated after the fact.
"""
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

import predict as predict_module
from query import answer_question
from trusted_auth import verify_apex_request

app = FastAPI(title="Olist Copilot API")


class QueryRequest(BaseModel):
    question: str


class PredictRequest(BaseModel):
    """The 12 form-collected features. SAME_STATE_FLAG and MAX_DISTANCE_KM
    (the other 2 of predict.py's 14 FEATURE_COLS) are derived server-side from
    PRIMARY_SELLER_STATE/CUSTOMER_STATE — see apex_api/predict.py's docstring."""
    PROMISED_DELIVERY_DAYS: float
    NUM_ITEMS: int
    TOTAL_PRICE: float
    TOTAL_FREIGHT_VALUE: float
    TOTAL_WEIGHT_G: float
    PRIMARY_PRODUCT_CATEGORY_NAME_ENGLISH: str
    PRIMARY_SELLER_STATE: str
    HEAVIEST_PRODUCT_WEIGHT_G: float
    HEAVIEST_PRODUCT_LENGTH_CM: float
    HEAVIEST_PRODUCT_HEIGHT_CM: float
    HEAVIEST_PRODUCT_WIDTH_CM: float
    CUSTOMER_STATE: str


@app.get("/health")
def health():
    """Unauthenticated liveness check — no DB/Claude calls, just process up."""
    return {"status": "ok"}


@app.post("/query")
def query(body: QueryRequest, caller: dict = Depends(verify_apex_request)):
    result = answer_question(body.question)
    result["caller"] = caller["username"]
    return result


@app.get("/predict/options")
def predict_options(caller: dict = Depends(verify_apex_request)):
    """Dropdown values for the Page 5 form's 3 categorical fields."""
    return predict_module.get_form_options()


@app.post("/predict")
def predict(body: PredictRequest, caller: dict = Depends(verify_apex_request)):
    try:
        result = predict_module.predict(body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    result["caller"] = caller["username"]
    return result