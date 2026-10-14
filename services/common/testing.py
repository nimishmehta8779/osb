"""Test helpers: mocked AWS with the same table shapes Terraform creates."""

import boto3


def make_tables(region: str = "ap-south-1"):
    ddb = boto3.resource("dynamodb", region_name=region)
    tenants = ddb.create_table(
        TableName="osb-dev-tenants",
        KeySchema=[{"AttributeName": "instance_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "instance_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    hostnames = ddb.create_table(
        TableName="osb-dev-hostnames",
        KeySchema=[{"AttributeName": "hostname", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "hostname", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    return tenants, hostnames
