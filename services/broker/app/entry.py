"""Process entrypoint: wires the broker's FastAPI app to real DynamoDB and SQS.

Run with: uvicorn services.broker.app.entry:app --host 0.0.0.0 --port 8000

Required environment: AWS_REGION, TENANTS_TABLE, HOSTNAMES_TABLE,
API_KEYS_TABLE, PROVISION_QUEUE_URL, ZONE_SUFFIX.

Auth is per-team API keys (X-Api-Key header), resolved against API_KEYS_TABLE
-- see services/common/api_keys.py. Keys are seeded via
`./deploy.sh seed-key` (see docs/operations.md); there is no
self-service key issuance yet.
"""

import os

from services.broker.app.main import create_app
from services.common.api_keys import ApiKeyStore
from services.common.aws import api_keys_table_from_env, queue_from_env, tenant_store_from_env

app = create_app(
    tenant_store_from_env(),
    queue_from_env(),
    zone_suffix=os.environ["ZONE_SUFFIX"],
    api_keys=ApiKeyStore(api_keys_table_from_env()),
)
