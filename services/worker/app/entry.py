"""Process entrypoint: wires the worker's SQS loop to real DynamoDB, SQS, and Route 53.

Run with: python -m services.worker.app.entry

Required environment: AWS_REGION, TENANTS_TABLE, HOSTNAMES_TABLE, PROVISION_QUEUE_URL.

DNS_ADAPTER selects the DNS adapter:
  - "route53" (default, used in Stage D): requires PRIVATE_ZONE_ID, NLB_DNS_NAME, NLB_ZONE_ID.
  - "noop" (Stage L3's local stack only): records calls instead of calling Route 53.

REACHABILITY_CHECK ("on" by default): whether a tenant's backend must answer a
TCP connection before the tenant is marked ACTIVE. Set to "off" only for a
local run where the backend genuinely isn't reachable from the worker yet.

DYNAMODB_ENDPOINT_URL and SQS_ENDPOINT_URL, if set, point at the Stage L3
local stand-ins instead of real AWS. Unset in Stage D.
"""

import logging
import os

import boto3

from services.common.aws import route53_client_from_env, tenant_store_from_env
from services.worker.app import worker
from services.worker.app.dns import PrivateDns
from services.worker.app.reachability import TcpReachability


def _build_dns():
    if os.environ.get("DNS_ADAPTER", "route53") == "noop":
        from services.worker.app.dns_noop import NoopDns

        return NoopDns()
    return PrivateDns(
        route53_client_from_env(),
        zone_id=os.environ["PRIVATE_ZONE_ID"],
        target_dns_name=os.environ["NLB_DNS_NAME"],
        target_zone_id=os.environ["NLB_ZONE_ID"],
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = tenant_store_from_env()
    dns = _build_dns()
    reachability = TcpReachability() if os.environ.get("REACHABILITY_CHECK", "on") != "off" else None
    sqs = boto3.client("sqs", region_name=os.environ["AWS_REGION"], endpoint_url=os.environ.get("SQS_ENDPOINT_URL"))
    worker.run_forever(sqs, os.environ["PROVISION_QUEUE_URL"], store, dns, reachability=reachability)


if __name__ == "__main__":
    main()
