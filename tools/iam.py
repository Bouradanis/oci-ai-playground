import os
import oci
import pandas as pd

_identity: oci.identity.IdentityClient | None = None


def _get_client() -> tuple[oci.identity.IdentityClient, str]:
    global _identity
    if _identity is None:
        config    = oci.config.from_file()
        _identity = oci.identity.IdentityClient(config)
    return _identity, os.environ['TENANCY_OCID']


def _fetch_all():
    """Fetch users, groups, and all memberships in one pass."""
    identity, tenancy_id = _get_client()

    users  = oci.pagination.list_call_get_all_results(
        identity.list_users,  compartment_id=tenancy_id
    ).data
    groups = oci.pagination.list_call_get_all_results(
        identity.list_groups, compartment_id=tenancy_id
    ).data

    all_memberships = []
    for g in groups:
        all_memberships.extend(
            oci.pagination.list_call_get_all_results(
                identity.list_user_group_memberships,
                compartment_id=tenancy_id,
                group_id=g.id,
            ).data
        )

    return identity, tenancy_id, users, groups, all_memberships


# ── DataFrame helpers (used by Streamlit) ─────────────────────────────────────

def get_users_df() -> pd.DataFrame:
    _, _, users, groups, memberships = _fetch_all()
    group_map = {g.id: g.name for g in groups}

    user_groups: dict[str, list[str]] = {u.id: [] for u in users}
    for m in memberships:
        if m.user_id in user_groups:
            user_groups[m.user_id].append(group_map.get(m.group_id, m.group_id))

    return pd.DataFrame([{
        'name':         u.name,
        'email':        u.email or '-',
        'state':        u.lifecycle_state,
        'mfa':          u.is_mfa_activated,
        'created':      str(u.time_created)[:10],
        'last_login':   str(u.last_successful_login_time)[:10] if u.last_successful_login_time else 'never',
        'groups':       ', '.join(user_groups[u.id]) or '(none)',
    } for u in users])


def get_groups_df() -> pd.DataFrame:
    _, _, users, groups, memberships = _fetch_all()
    user_map = {u.id: u.name for u in users}

    group_members: dict[str, list[str]] = {g.id: [] for g in groups}
    for m in memberships:
        if m.group_id in group_members:
            group_members[m.group_id].append(user_map.get(m.user_id, m.user_id))

    return pd.DataFrame([{
        'group':       g.name,
        'description': g.description or '-',
        'state':       g.lifecycle_state,
        'created':     str(g.time_created)[:10],
        'members':     ', '.join(group_members[g.id]) or '(none)',
    } for g in groups])


def add_user_to_group(user_name: str, group_name: str) -> str:
    identity, tenancy_id, users, groups, _ = _fetch_all()
    user_map  = {u.name.lower(): u for u in users}
    group_map = {g.name.lower(): g for g in groups}

    u = user_map.get(user_name.lower())
    g = group_map.get(group_name.lower())
    if not u:
        return f"User '{user_name}' not found."
    if not g:
        return f"Group '{group_name}' not found."

    try:
        identity.add_user_to_group(
            oci.identity.models.AddUserToGroupDetails(user_id=u.id, group_id=g.id)
        )
        return f"✓ {u.name} added to {g.name}"
    except oci.exceptions.ServiceError as e:
        if e.status == 409:
            return f"{u.name} is already a member of {g.name}"
        return f"OCI error: {e.message}"


def remove_user_from_group(user_name: str, group_name: str) -> str:
    identity, tenancy_id, users, groups, memberships = _fetch_all()
    user_map  = {u.name.lower(): u for u in users}
    group_map = {g.name.lower(): g for g in groups}

    u = user_map.get(user_name.lower())
    g = group_map.get(group_name.lower())
    if not u:
        return f"User '{user_name}' not found."
    if not g:
        return f"Group '{group_name}' not found."

    match = [m for m in memberships if m.user_id == u.id and m.group_id == g.id]
    if not match:
        return f"{u.name} is not a member of {g.name}"

    try:
        identity.remove_user_from_group(match[0].id)
        return f"✓ {u.name} removed from {g.name}"
    except oci.exceptions.ServiceError as e:
        return f"OCI error: {e.message}"


# ── MCP string-returning versions ─────────────────────────────────────────────

def list_iam_users() -> str:
    df = get_users_df()
    lines = ["| NAME | EMAIL | STATE | MFA | CREATED | LAST LOGIN | GROUPS |", "|---|---|---|---|---|---|---|"]
    for _, r in df.iterrows():
        lines.append(f"| {r['name']} | {r['email']} | {r['state']} | {'Yes' if r['mfa'] else 'No'} | {r['created']} | {r['last_login']} | {r['groups']} |")
    lines.append(f"\n_Total: {len(df)} users_")
    return "\n".join(lines)


def list_iam_groups() -> str:
    df = get_groups_df()
    lines = ["| GROUP | DESCRIPTION | STATE | CREATED | MEMBERS |", "|---|---|---|---|---|"]
    for _, r in df.iterrows():
        lines.append(f"| {r['group']} | {r['description']} | {r['state']} | {r['created']} | {r['members']} |")
    lines.append(f"\n_Total: {len(df)} groups_")
    return "\n".join(lines)
