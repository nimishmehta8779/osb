"""Builds the real AWS-backed dependencies from environment variables.

Tests never import this: they construct TenantStore and the saga directly
against moto or fakes. This module exists only for the broker and worker
entrypoints that run as real processes.
"""

import json
import os

import boto3

from services.common.tenants import TenantStore


def _region() -> str:
    return os.environ["AWS_REGION"]


def tenant_store_from_env() -> TenantStore:
    # DYNAMODB_ENDPOINT_URL is set only for the Stage L3 local stack (DynamoDB
    # Local). It's unset in Stage D, so boto3 talks to real AWS as usual.
    ddb = boto3.resource("dynamodb", region_name=_region(), endpoint_url=os.environ.get("DYNAMODB_ENDPOINT_URL"))
    tenants = ddb.Table(os.environ["TENANTS_TABLE"])
    hostnames = ddb.Table(os.environ["HOSTNAMES_TABLE"])
    return TenantStore(tenants, hostnames)


def api_keys_table_from_env():
    ddb = boto3.resource("dynamodb", region_name=_region(), endpoint_url=os.environ.get("DYNAMODB_ENDPOINT_URL"))
    return ddb.Table(os.environ["API_KEYS_TABLE"])


class SqsQueue:
    """The broker's send-only view of the provisioning queue."""

    def __init__(self, client, queue_url: str):
        self._sqs = client
        self._queue_url = queue_url

    def send(self, message: dict) -> None:
        self._sqs.send_message(QueueUrl=self._queue_url, MessageBody=json.dumps(message))


def queue_from_env() -> SqsQueue:
    # SQS_ENDPOINT_URL is the Stage L3 equivalent of DYNAMODB_ENDPOINT_URL, for ElasticMQ.
    sqs = boto3.client("sqs", region_name=_region(), endpoint_url=os.environ.get("SQS_ENDPOINT_URL"))
    return SqsQueue(sqs, os.environ["PROVISION_QUEUE_URL"])


def route53_client_from_env():
    return boto3.client("route53", region_name=_region())
