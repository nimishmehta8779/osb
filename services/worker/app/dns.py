"""Route 53 adapter for tenant hostnames, in the private zone only.

Both operations are idempotent: upsert overwrites, and delete is a no-op when the
record is already gone. That's what makes saga retries and compensations safe.
"""

from botocore.exceptions import ClientError


class PrivateDns:
    def __init__(self, client, zone_id: str, target_dns_name: str, target_zone_id: str):
        self._r53 = client
        self._zone_id = zone_id
        self._target_dns = target_dns_name
        self._target_zone = target_zone_id

    def upsert(self, hostname: str) -> None:
        self._r53.change_resource_record_sets(
            HostedZoneId=self._zone_id,
            ChangeBatch={
                "Changes": [
                    {
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": hostname,
                            "Type": "A",
                            "AliasTarget": {
                                "HostedZoneId": self._target_zone,
                                "DNSName": self._target_dns,
                                "EvaluateTargetHealth": True,
                            },
                        },
                    }
                ]
            },
        )

    def delete(self, hostname: str) -> None:
        resp = self._r53.list_resource_record_sets(
            HostedZoneId=self._zone_id, StartRecordName=hostname, StartRecordType="A", MaxItems="1"
        )
        for rec in resp.get("ResourceRecordSets", []):
            if rec["Name"].rstrip(".") == hostname and rec["Type"] == "A":
                try:
                    self._r53.change_resource_record_sets(
                        HostedZoneId=self._zone_id,
                        ChangeBatch={"Changes": [{"Action": "DELETE", "ResourceRecordSet": rec}]},
                    )
                except ClientError as e:
                    # Already deleted by a concurrent run. That's the state we want.
                    if e.response["Error"]["Code"] != "InvalidChangeBatch":
                        raise
