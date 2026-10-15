"""The SDK's response types must not promise what the API does not send.

Each SDK TypedDict is checked against its API schema in openapi_schemas.json (written by
scripts/sync_openapi_contract.py from nozle-openapi.yaml): every key the SDK declares must
exist in the schema, and every key the SDK marks required must be required by the schema.
"""

import json
from pathlib import Path

import pytest

from nozle import types

SCHEMAS = json.loads((Path(__file__).parent / "openapi_schemas.json").read_text())

CONTRACT = {
    "UsageTrackResult": "CreditsUsageTrackResponse",
    "UsageCheckResult": "CreditsUsageCheckResponse",
    "CanResult": "PlatformCanResponse",
    "PingResult": "PlatformPingResponse",
    "CostEventAccepted": "CostEventAccepted",
    "CreditBalance": "CreditsCustomerBalance",
    "CreditBalances": "CreditsCustomerBalanceList",
    "CreditOperationPage": "CreditsCustomerOperationPage",
    "CustomerEntity": "CreditsEntity",
    "CustomerEntityPage": "CreditsEntityPage",
    "EntityCreditBalance": "CreditsEntityBalance",
    "EntityCreditBalances": "CreditsEntityBalanceList",
    "SubscriptionOptions": "BillingSubscriptionOptionsResponse",
}


@pytest.mark.parametrize("sdk_type,schema", sorted(CONTRACT.items()))
def test_sdk_type_fits_the_api_schema(sdk_type: str, schema: str) -> None:
    sdk = getattr(types, sdk_type)
    api = SCHEMAS[schema]
    unknown = sorted(set(sdk.__annotations__) - set(api["properties"]))
    not_guaranteed = sorted(set(sdk.__required_keys__) - set(api["required"]))
    assert not unknown, f"{sdk_type} declares keys the API never sends: {unknown}"
    assert not not_guaranteed, f"{sdk_type} requires keys the API may omit: {not_guaranteed}"
