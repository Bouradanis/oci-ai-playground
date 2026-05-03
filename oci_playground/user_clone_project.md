# User Clone Project — Context & Goal

## What this project is

A Python tool that takes two OCI users — a **source user** and a **target (test) user** — and makes the target identical to the source in terms of:

1. **OCI IAM groups** ✅ — tested and working (see below)
2. **OAC roles** 🔲 — next to implement
3. **Database data access (VPD)** 🔲 — next to implement

The use case: in a work environment with multiple Oracle Analytics Cloud environments (dev, demo, production), quickly spin up a test user that mirrors a real user's full access profile without touching production manually.

---

## Part 1 — OCI IAM Groups (DONE)

### What was built & tested

A Python function that:
1. Fetches all IAM groups the source user belongs to
2. Adds the target user to the same groups

### How OCI IAM works

- Every OCI user belongs to one or more **groups** (e.g. `Administrators`, `ds_group`)
- Groups are managed via the **OCI Identity Client** (`oci.identity.IdentityClient`)
- The tenancy OCID is the root compartment for all IAM calls
- Credentials come from `~/.oci/config` (standard OCI SDK config file)

### Key SDK calls

```python
import oci

config     = oci.config.from_file()
identity   = oci.identity.IdentityClient(config)
TENANCY_ID = os.environ['TENANCY_OCID']

# List all users
users = oci.pagination.list_call_get_all_results(
    identity.list_users, compartment_id=TENANCY_ID
).data

# List all groups
groups = oci.pagination.list_call_get_all_results(
    identity.list_groups, compartment_id=TENANCY_ID
).data

# List memberships for a group
memberships = oci.pagination.list_call_get_all_results(
    identity.list_user_group_memberships,
    compartment_id=TENANCY_ID,
    group_id=group.id,
).data

# Add user to group
identity.add_user_to_group(
    oci.identity.models.AddUserToGroupDetails(
        user_id=user.id,
        group_id=group.id,
    )
)

# Remove user from group
identity.remove_user_from_group(membership_id)
```

### Clone logic (pseudocode)

```python
def clone_iam_groups(source_username: str, target_username: str):
    # 1. Get source user's current groups
    # 2. Get target user's current groups
    # 3. Add target to any group source has that target doesn't
    # (optionally: remove target from groups source doesn't have)
```

### Tested via

- Streamlit app (`app.py`) with a confirmation step before mutations
- `tools/iam.py` contains the reusable functions: `get_users_df()`, `get_groups_df()`, `add_user_to_group()`, `remove_user_from_group()`
- Notebook: `oci_playground/oci_iam_playground.ipynb`

---

## Part 2 — OAC Roles (TODO)

### How OAC roles work

Oracle Analytics Cloud roles (e.g. `BI Author`, `BI Consumer`, `BI Administrator`) are **not** managed via the OAC REST API. They are managed through **OCI Identity Domains** (formerly IDCS — Oracle Identity Cloud Service).

OAC registers itself as an **application** inside Identity Domains, and its roles appear as **app roles** there.

### API to use

**OCI Identity Domains REST API** (not the OCI Python SDK — raw HTTP with `requests`):

```
Base URL: https://<identity-domain-host>/admin/v1
```

Key endpoints:
```
GET  /Users?filter=userName eq "<username>"     → get user + their app roles
GET  /AppRoles?filter=app.displayName eq "OAC"  → list all OAC app roles
POST /AppRoles/<role_id>/members                 → assign role to user
DELETE /AppRoles/<role_id>/members/<member_id>   → remove role from user
```

### Authentication

OAuth2 Bearer token from Identity Domains:
```
POST https://<idcs-host>/oauth2/v1/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials&scope=urn:opc:idm:__myscopes__
```
Requires a **Confidential Application** registered in Identity Domains with the right admin scopes.

### Clone logic (pseudocode)

```python
def clone_oac_roles(source_username: str, target_username: str):
    # 1. Get OAuth2 token from Identity Domains
    # 2. Find source user in Identity Domains → get their OAC app role memberships
    # 3. Find target user in Identity Domains
    # 4. Assign same OAC app roles to target user
```

---

## Part 3 — Database Data Access / VPD (TODO)

### How Oracle VPD works

Oracle **Virtual Private Database (VPD)** restricts which rows a user can see using row-level security policies. A policy function is attached to a table via `DBMS_RLS.ADD_POLICY`. The function returns a `WHERE` clause that Oracle appends to every query on that table.

Typically, a user's data access is controlled by a value stored in their session context (e.g. `SYS_CONTEXT('myctx', 'vpd_id')`), which is set at login time via a logon trigger or application context.

### What needs to be replicated

To clone data access from source to target:
1. Find what `vpd_id` (or equivalent context value) the source user has
2. Assign the same `vpd_id` to the target user
3. This is usually stored in a user profile/mapping table in the database

### Likely implementation

```python
def clone_vpd_access(source_username: str, target_username: str, conn):
    # 1. Query the user-to-vpd_id mapping table for source user
    # 2. Insert/update the same mapping for target user
    # 3. Verify by querying as target user and checking visible rows
```

The exact table name depends on how VPD is configured in the target environment.

---

## Tech stack

| Component | Choice |
|---|---|
| OCI IAM | `oci` Python SDK |
| OAC roles | `requests` — raw HTTP to Identity Domains REST API |
| Database access | `oracledb` — thin mode, connects to Oracle ADB |
| OCI credentials | `~/.oci/config` + `TENANCY_OCID` in `.env` |
| DB credentials | OCI Vault secrets, decoded via `base64` + `json` |

## Environment variables needed (`.env`)

```
TENANCY_OCID=ocid1.tenancy...
OML_USER_CREDS_SECRET_OCID=ocid1.vaultsecret...   # DB credentials
IDCS_CLIENT_ID=...                                  # for OAC role API (Part 2)
IDCS_CLIENT_SECRET=...                              # for OAC role API (Part 2)
IDCS_BASE_URL=https://<idcs-host>                   # Identity Domains host
OAC_APP_NAME=...                                    # OAC app name in Identity Domains
TNS_ADMIN=/path/to/wallet                           # Oracle wallet for DB connection
```
