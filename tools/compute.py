import os
import oci
import pandas as pd

# ── Free tier hard limits ─────────────────────────────────────────────────────
ALLOWED_SHAPES = {"VM.Standard.A1.Flex", "VM.Standard.E2.1.Micro"}
SHAPE_LIMITS = {
    "VM.Standard.A1.Flex":    {"max_ocpus": 4,  "max_memory_gb": 24},
    "VM.Standard.E2.1.Micro": {"max_ocpus": 1,  "max_memory_gb": 1},
}

COMPARTMENT_ID = "ocid1.compartment.oc1..aaaaaaaaibazzyu4zv6qnuehcpqzby5jgbucbivhrjhyqx6vmx7fqxiy37uq"
SUBNET_ID      = "ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaadozudkvgvwkshcotu42syiamwiw5w4prd6it5dbrs3ftlu6scooa"
IMAGE_ID       = "ocid1.image.oc1.eu-frankfurt-1.aaaaaaaaimlbvu2dnd46l4gmgpcykuuqm6v52u67tqki7hxmptppe4wdhwea"
SSH_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOhcpXmXoCtt7LnzscZZjPhBn5UCKdH1ZSktVIbQLvo0 olist-vm"

AVAILABILITY_DOMAINS = [
    "lXgJ:EU-FRANKFURT-1-AD-1",
    "lXgJ:EU-FRANKFURT-1-AD-2",
    "lXgJ:EU-FRANKFURT-1-AD-3",
]

_compute: oci.core.ComputeClient | None = None
_network: oci.core.VirtualNetworkClient | None = None


def _get_clients():
    global _compute, _network
    if _compute is None:
        config   = oci.config.from_file()
        _compute = oci.core.ComputeClient(config)
        _network = oci.core.VirtualNetworkClient(config)
    return _compute, _network


def get_vms_df() -> pd.DataFrame:
    compute, network = _get_clients()
    instances = oci.pagination.list_call_get_all_results(
        compute.list_instances, compartment_id=COMPARTMENT_ID
    ).data

    rows = []
    for i in instances:
        if i.lifecycle_state == 'TERMINATED':
            continue
        public_ip = '-'
        try:
            vnics = compute.list_vnic_attachments(COMPARTMENT_ID, instance_id=i.id).data
            if vnics:
                vnic = network.get_vnic(vnics[0].vnic_id).data
                public_ip = vnic.public_ip or '-'
        except Exception:
            pass
        rows.append({
            'name':      i.display_name,
            'shape':     i.shape,
            'state':     i.lifecycle_state,
            'ad':        i.availability_domain.split(':')[-1],
            'public_ip': public_ip,
            'created':   str(i.time_created)[:10],
            'id':        i.id,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=['name', 'shape', 'state', 'ad', 'public_ip', 'created', 'id']
    )


def create_vm(display_name: str = 'olist-mcp-vm',
              shape: str = 'VM.Standard.A1.Flex',
              ocpus: int = 2,
              memory_gb: int = 12) -> str:
    # Hard free-tier enforcement — cannot be bypassed by Claude
    if shape not in ALLOWED_SHAPES:
        return f"Blocked: '{shape}' is not a free tier shape. Allowed: {', '.join(ALLOWED_SHAPES)}"
    limits = SHAPE_LIMITS[shape]
    if ocpus > limits['max_ocpus']:
        return f"Blocked: {shape} allows max {limits['max_ocpus']} OCPUs."
    if memory_gb > limits['max_memory_gb']:
        return f"Blocked: {shape} allows max {limits['max_memory_gb']}GB memory."

    compute, _ = _get_clients()
    for ad in AVAILABILITY_DOMAINS:
        try:
            shape_config = (
                oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=ocpus, memory_in_gbs=memory_gb
                ) if shape == 'VM.Standard.A1.Flex' else None
            )
            instance = compute.launch_instance(
                oci.core.models.LaunchInstanceDetails(
                    display_name=display_name,
                    compartment_id=COMPARTMENT_ID,
                    availability_domain=ad,
                    shape=shape,
                    shape_config=shape_config,
                    source_details=oci.core.models.InstanceSourceViaImageDetails(
                        source_type="image", image_id=IMAGE_ID,
                    ),
                    create_vnic_details=oci.core.models.CreateVnicDetails(
                        subnet_id=SUBNET_ID, assign_public_ip=True,
                    ),
                    metadata={"ssh_authorized_keys": SSH_PUBLIC_KEY},
                )
            ).data
            return (f"✓ VM '{display_name}' created in {ad}\n"
                    f"  Shape: {shape}  {ocpus} OCPU / {memory_gb}GB\n"
                    f"  State: {instance.lifecycle_state}\n"
                    f"  ID: {instance.id}")
        except oci.exceptions.ServiceError as e:
            if 'capacity' in str(e).lower():
                continue
            return f"OCI error: {e.message}"
        except Exception as e:
            return f"Error: {type(e).__name__}: {e}"
    return "No capacity available in any availability domain. Try again later."


def _find_instance_id(name: str) -> tuple[str | None, str]:
    df = get_vms_df()
    match = df[df['name'].str.lower() == name.lower()]
    if match.empty:
        available = df['name'].tolist()
        return None, f"VM '{name}' not found. Available: {available}"
    return match.iloc[0]['id'], ""


def start_vm(name: str) -> str:
    compute, _ = _get_clients()
    instance_id, err = _find_instance_id(name)
    if not instance_id:
        return err
    try:
        compute.instance_action(instance_id, 'START')
        return f"✓ VM '{name}' is starting"
    except oci.exceptions.ServiceError as e:
        return f"OCI error: {e.message}"


def stop_vm(name: str) -> str:
    compute, _ = _get_clients()
    instance_id, err = _find_instance_id(name)
    if not instance_id:
        return err
    try:
        compute.instance_action(instance_id, 'SOFTSTOP')
        return f"✓ VM '{name}' is stopping"
    except oci.exceptions.ServiceError as e:
        return f"OCI error: {e.message}"


def delete_vm(name: str) -> str:
    compute, _ = _get_clients()
    instance_id, err = _find_instance_id(name)
    if not instance_id:
        return err
    try:
        compute.terminate_instance(instance_id)
        return f"✓ VM '{name}' is being terminated"
    except oci.exceptions.ServiceError as e:
        return f"OCI error: {e.message}"
