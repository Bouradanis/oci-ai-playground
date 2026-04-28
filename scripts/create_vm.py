"""
Auto-retry script to create an OCI ARM (A1.Flex) instance.
Tries all 3 availability domains in rotation until capacity is available.
Run with: python scripts/create_vm.py
"""
import oci
import time
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / '.env')

# ── Config ────────────────────────────────────────────────────────────────────
COMPARTMENT_ID = "ocid1.compartment.oc1..aaaaaaaaibazzyu4zv6qnuehcpqzby5jgbucbivhrjhyqx6vmx7fqxiy37uq"
SUBNET_ID      = "ocid1.subnet.oc1.eu-frankfurt-1.aaaaaaaadozudkvgvwkshcotu42syiamwiw5w4prd6it5dbrs3ftlu6scooa"
IMAGE_ID       = "ocid1.image.oc1.eu-frankfurt-1.aaaaaaaaimlbvu2dnd46l4gmgpcykuuqm6v52u67tqki7hxmptppe4wdhwea"
SSH_PUBLIC_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOhcpXmXoCtt7LnzscZZjPhBn5UCKdH1ZSktVIbQLvo0 olist-vm"

AVAILABILITY_DOMAINS = [
    "lXgJ:EU-FRANKFURT-1-AD-1",
    "lXgJ:EU-FRANKFURT-1-AD-2",
    "lXgJ:EU-FRANKFURT-1-AD-3",
]

OCPU        = 2
MEMORY_GB   = 12
RETRY_DELAY = 15  # seconds between attempts

# ── OCI client ────────────────────────────────────────────────────────────────
config = oci.config.from_file()
compute = oci.core.ComputeClient(config)


def try_create(ad: str):
    print(f"  Trying {ad}...", end=" ", flush=True)
    try:
        response = compute.launch_instance(
            oci.core.models.LaunchInstanceDetails(
                display_name="olist-mcp-vm",
                compartment_id=COMPARTMENT_ID,
                availability_domain=ad,
                shape="VM.Standard.A1.Flex",
                shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(
                    ocpus=OCPU,
                    memory_in_gbs=MEMORY_GB,
                ),
                source_details=oci.core.models.InstanceSourceViaImageDetails(
                    source_type="image",
                    image_id=IMAGE_ID,
                ),
                create_vnic_details=oci.core.models.CreateVnicDetails(
                    subnet_id=SUBNET_ID,
                    assign_public_ip=True,
                ),
                metadata={"ssh_authorized_keys": SSH_PUBLIC_KEY},
            )
        )
        return response.data
    except oci.exceptions.ServiceError as e:
        if "Out of capacity" in str(e) or "out of host capacity" in str(e).lower():
            print("no capacity.")
        else:
            print(f"error: {e.message}")
        return None
    except Exception as e:
        print(f"network error: {type(e).__name__}")
        return None


def main():
    print("OCI ARM instance creator — retrying until capacity is available")
    print(f"Shape: VM.Standard.A1.Flex  {OCPU} OCPU / {MEMORY_GB}GB RAM")
    print(f"Retry interval: {RETRY_DELAY}s\n")

    attempt = 0
    while True:
        attempt += 1
        print(f"[Attempt {attempt}]")
        for ad in AVAILABILITY_DOMAINS:
            instance = try_create(ad)
            if instance:
                print(f"\n✅ Instance created!")
                print(f"   ID:    {instance.id}")
                print(f"   AD:    {instance.availability_domain}")
                print(f"   State: {instance.lifecycle_state}")
                print(f"\nWaiting for public IP (may take ~2 min)...")
                time.sleep(60)
                vnic_attachments = compute.list_vnic_attachments(
                    COMPARTMENT_ID, instance_id=instance.id
                ).data
                if vnic_attachments:
                    network = oci.core.VirtualNetworkClient(config)
                    vnic = network.get_vnic(vnic_attachments[0].vnic_id).data
                    print(f"   Public IP: {vnic.public_ip}")
                return

        print(f"  All ADs full. Waiting {RETRY_DELAY}s before next attempt...\n")
        time.sleep(RETRY_DELAY)


if __name__ == "__main__":
    main()
