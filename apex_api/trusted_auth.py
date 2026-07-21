"""Trusted-subsystem authentication for APEX -> FastAPI calls.

Why this exists: investigated (KAN-6) whether APEX's OIDC Social Sign-In
retains a forwardable OAuth access/id token that FastAPI could independently
validate against the same userinfo endpoint auth.py uses. Confirmed on two
separate real, freshly-verified logins that no such token is retrievable —
see the Confluence gotcha "APEX Gotcha — Social Sign-In Does Not Expose a
Forwardable OAuth Token to PL/SQL".

Design used instead ("trusted subsystem" pattern): a shared secret known only
to APEX and FastAPI. APEX signs {username, groups, timestamp} with HMAC-SHA256
using its copy of the secret (read from an Application Setting, computed in
the Post-Authentication procedure or at request time) and sends it as headers
on the apex_web_service.make_rest_request() call. FastAPI recomputes the same
HMAC with its own copy of the secret (env var, never committed) and rejects
the request if the signature doesn't match or the timestamp is stale. This
authenticates "this call really came from our APEX app" — it does NOT
independently verify the end user's identity the way validating a real OIDC
token would, since APEX's asserted username/groups are trusted once the
signature checks out. That's the accepted trade-off per the KAN-6 discussion.
"""
import hashlib
import hmac
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Header, HTTPException

load_dotenv(Path(__file__).parent.parent / '.env')

_SHARED_SECRET = os.environ["APEX_FASTAPI_SHARED_SECRET"].encode()

# Reject requests signed more than this many seconds ago (or from the
# future, e.g. clock skew) — narrows the window in which a captured request
# could be replayed.
_TIMESTAMP_TOLERANCE_SECONDS = 300

# Mirrors auth.py's ROLE_GROUPS. Kept as a separate copy deliberately: this
# is a different trust boundary (APEX asserts the groups string; FastAPI
# decides the role from it), not a shared import, so a change on one side
# doesn't silently change the other's behavior.
ROLE_GROUPS = {
    "olist_admins": "admin",
    "olist_users": "user",
}


def sign(username: str, groups: str, timestamp: str) -> str:
    """Same canonicalization must be used by both the APEX PL/SQL signer and
    this verifier: newline-joined username, groups, timestamp."""
    payload = f"{username}\n{groups}\n{timestamp}".encode()
    return hmac.new(_SHARED_SECRET, payload, hashlib.sha256).hexdigest()


def _resolve_role(groups: str) -> str | None:
    # G_APEX_GROUPS format (per oidc_post_auth): colon-separated, trailing
    # colon, e.g. "ds_group:Administrators:olist_admins:"
    names = [g for g in groups.split(":") if g]
    for name in names:
        if name in ROLE_GROUPS:
            return ROLE_GROUPS[name]
    return None


async def verify_apex_request(
    x_apex_username: str = Header(...),
    x_apex_groups: str = Header(...),
    x_apex_timestamp: str = Header(...),
    x_apex_signature: str = Header(...),
) -> dict:
    """FastAPI dependency: independently verifies the HMAC signature APEX
    attached to this request. Raises 401/403 on any failure. Only returns
    (and only should be trusted) once both the signature and the timestamp
    window check out."""
    expected_sig = sign(x_apex_username, x_apex_groups, x_apex_timestamp)
    if not hmac.compare_digest(expected_sig, x_apex_signature):
        raise HTTPException(status_code=401, detail="Invalid signature.")

    try:
        ts = int(x_apex_timestamp)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid timestamp.")

    if abs(time.time() - ts) > _TIMESTAMP_TOLERANCE_SECONDS:
        raise HTTPException(status_code=401, detail="Request expired or timestamp out of acceptable range.")

    role = _resolve_role(x_apex_groups)
    if role is None:
        raise HTTPException(status_code=403, detail="User is not a member of olist_admins or olist_users.")

    return {"username": x_apex_username, "groups": x_apex_groups, "role": role}