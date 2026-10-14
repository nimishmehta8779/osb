"""Stage L3 only: creates the tenant/hostname/api_keys tables and the
provisioning queue against the local stand-ins (DynamoDB Local, ElasticMQ),
with the same shapes Terraform creates in infra/modules/provisioning. Run
once, before the broker or worker start. Also seeds one local-dev API key so
the stack is usable immediately without a separate seeding step.
"""

import hashlib
import os

import boto3

region = os.environ["AWS_REGION"]

ddb = boto3.client("dynamodb", region_name=region, endpoint_url=os.environ["DYNAMODB_ENDPOINT_URL"])
for name, key in [
    (os.environ["TENANTS_TABLE"], "instance_id"),
    (os.environ["HOSTNAMES_TABLE"], "hostname"),
    (os.environ["API_KEYS_TABLE"], "api_key_hash"),
]:
    try:
        ddb.create_table(
            TableName=name,
            KeySchema=[{"AttributeName": key, "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": key, "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        print(f"created table {name}")
    except ddb.exceptions.ResourceInUseException:
        print(f"table {name} already exists")

# Seed one local-dev team key (see docs/operations.md for production key seeding via ./deploy.sh seed-key).
local_key = os.environ.get("LOCAL_DEV_API_KEY", "local-dev-only")
local_key_hash = hashlib.sha256(local_key.encode()).hexdigest()
ddb.put_item(
    TableName=os.environ["API_KEYS_TABLE"],
    Item={"api_key_hash": {"S": local_key_hash}, "team_id": {"S": "local-dev-team"}, "active": {"BOOL": True}},
)
print(f"seeded local-dev API key for team 'local-dev-team'")

sqs = boto3.client("sqs", region_name=region, endpoint_url=os.environ["SQS_ENDPOINT_URL"])
queue_name = os.environ["PROVISION_QUEUE_URL"].rsplit("/", 1)[-1]
sqs.create_queue(QueueName=queue_name)
print(f"queue {queue_name} ready")
