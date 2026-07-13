import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / '.env')

_STATE_TTL = 300  # seconds

DOMAIN_URL    = os.environ["OCI_DOMAIN_URL"]
CLIENT_ID     = os.environ["OCI_OAUTH_CLIENT_ID"]
CLIENT_SECRET = os.environ["OCI_OAUTH_CLIENT_SECRET"]
REDIRECT_URI  = os.environ["OCI_OAUTH_REDIRECT_URI"]

AUTHORIZE_URL = f"{DOMAIN_URL}/oauth2/v1/authorize"
TOKEN_URL     = f"{DOMAIN_URL}/oauth2/v1/token"
USERINFO_URL  = f"{DOMAIN_URL}/oauth2/v1/userinfo"
LOGOUT_URL    = f"{DOMAIN_URL}/oauth2/v1/userlogout"

# IAM group name -> app role
ROLE_GROUPS = {
    "olist_admins": "admin",
    "olist_users":  "user",
}


def new_state() -> str:
    """Self-verifying CSRF token — HMAC-signed timestamp, no server-side storage
    needed, since Streamlit's session_state doesn't reliably survive the full
    page navigation to OCI's login page and back."""
    ts = str(int(time.time()))
    sig = hmac.new(CLIENT_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def verify_state(state: str) -> bool:
    try:
        ts, sig = state.split(".", 1)
        expected = hmac.new(CLIENT_SECRET.encode(), ts.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(sig, expected) and (time.time() - int(ts)) < _STATE_TTL
    except Exception:
        return False


def build_authorize_url(state: str) -> str:
    params = {
        "client_id":     CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  REDIRECT_URI,
        "scope":         "openid groups",
        "state":         state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _exchange_code(code: str) -> dict:
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": REDIRECT_URI,
        },
        auth=(CLIENT_ID, CLIENT_SECRET),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _get_userinfo(access_token: str) -> dict:
    resp = requests.get(
        USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


def _resolve_role(groups: list[str]) -> str | None:
    for g in groups:
        if g in ROLE_GROUPS:
            return ROLE_GROUPS[g]
    return None


def _extract_group_name(g) -> str:
    if isinstance(g, str):
        return g
    if isinstance(g, dict):
        for key in ("value", "display", "name", "groupName", "displayName"):
            if key in g:
                return g[key]
    return str(g)


def login(code: str) -> dict:
    """Exchange an authorization code for an identity: {username, groups, role}."""
    tokens = _exchange_code(code)
    info = _get_userinfo(tokens["access_token"])

    username = info.get("user_name") or info.get("preferred_username") or info.get("sub")

    groups_raw = info.get("groups", [])
    groups = [_extract_group_name(g) for g in groups_raw]

    return {
        "username": username,
        "groups":   groups,
        "role":     _resolve_role(groups),
        "id_token": tokens.get("id_token"),
    }


def build_logout_url(id_token: str | None) -> str:
    """Ends the actual OCI browser SSO session (not just this app's login),
    so the next login prompts for credentials instead of silently
    re-authenticating as whoever was previously logged in."""
    params = {"post_logout_redirect_uri": REDIRECT_URI}
    if id_token:
        params["id_token_hint"] = id_token
    return f"{LOGOUT_URL}?{urlencode(params)}"