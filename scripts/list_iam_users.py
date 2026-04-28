"""
List all IAM users and groups in the OCI tenancy.
Run with: python scripts/list_iam_users.py
"""
import os
import oci
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

config   = oci.config.from_file()
identity = oci.identity.IdentityClient(config)



users = oci.pagination.list_call_get_all_results(
    identity.list_users,
    compartment_id=os.environ['TENANCY_OCID'],
).data

print(f"{'NAME':<30} {'EMAIL':<35} {'STATE':<12} {'MFA':<5} {'CREATED'}")
print("-" * 100)

for u in users:
    print(
        f"{u.name:<30} "
        f"{(u.email or '-'):<35} "
        f"{u.lifecycle_state:<12} "
        f"{'Yes' if u.is_mfa_activated else 'No':<5} "
        f"{str(u.time_created)[:10]}"
    )

print(f"\nTotal users: {len(users)}")

# ── Groups ────────────────────────────────────────────────────────────────────
groups = oci.pagination.list_call_get_all_results(
    identity.list_groups,
    compartment_id=os.environ['TENANCY_OCID'],
).data

print(f"\n\n{'GROUP NAME':<35} {'DESCRIPTION':<45} {'STATE':<12} {'CREATED'}")
print("-" * 100)

for g in groups:
    print(
        f"{g.name:<35} "
        f"{(g.description or '-')[:44]:<45} "
        f"{g.lifecycle_state:<12} "
        f"{str(g.time_created)[:10]}"
    )

print(f"\nTotal groups: {len(groups)}")

# ── Group memberships ─────────────────────────────────────────────────────────
print("\n\n── Group memberships ──")
for g in groups:
    members = oci.pagination.list_call_get_all_results(
        identity.list_user_group_memberships,
        compartment_id=os.environ['TENANCY_OCID'],
        group_id=g.id,
    ).data
    user_ids  = {m.user_id for m in members}
    user_names = [u.name for u in users if u.id in user_ids]
    members_str = ", ".join(user_names) if user_names else "(no members)"
    print(f"  {g.name}: {members_str}")
