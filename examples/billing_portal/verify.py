"""Exercise a dedicated fixture through either merchant HTTP integration."""

import json
import os
import uuid
from datetime import datetime
from typing import Any

import requests

base = os.environ.get("MERCHANT_URL", "http://localhost:4243")
origin = os.environ.get("MERCHANT_ORIGIN", base)
external_id = os.environ["DEMO_SUBSCRIPTION_ID"]
assert external_id.startswith("sdk-cancel-test-"), "Use a dedicated sdk-cancel-test-* fixture"
assert os.environ["DEMO_CUSTOMER_ID"].startswith("sdk-cancel-test-")
browser = requests.Session()
browser.headers.update({"Origin": origin})


def post(path: str, body: dict[str, Any], status: int = 200) -> Any:
    response = browser.post(base + path, json=body, timeout=30)
    assert response.status_code == status, f"{path} returned HTTP {response.status_code}"
    return response.json()


post("/api/billing/session", {}, 401)
post("/api/login", {"token": os.environ["DEMO_LOGIN_TOKEN"]})
session = post("/api/billing/session", {})


def state() -> dict[str, Any]:
    response = requests.post(
        session["apiUrl"].rstrip("/") + "/graphql",
        headers={"customer-portal-token": session["token"]},
        json={
            "query": """query { customerPortalSubscriptions(status: [active], limit: 100) {
                collection { id externalId status endingAt plan { code } nextPlan { code } } } }"""
        },
        timeout=30,
    )
    assert response.status_code == 200, "Portal GraphQL failed"
    data = response.json()
    assert not data.get("errors"), "Portal GraphQL rejected state read"
    for subscription in data["data"]["customerPortalSubscriptions"]["collection"]:
        if subscription["externalId"] == external_id:
            return dict(subscription)
    raise AssertionError("Dedicated subscription missing from authenticated customer's portal")


before = state()
assert before["endingAt"] is None, "Fixture must start renewing; preserve existing cancellation"
assert before["nextPlan"] is None, "Fixture must not have an existing pending plan change"
preview = post(
    "/api/billing/cancellation/preview", {"subscriptionId": external_id, "operation": "cancel"}
)
cancel = {
    "subscriptionId": external_id,
    "operation": "cancel",
    "expectedEffectiveAt": preview["effectiveAt"],
    "idempotencyKey": str(uuid.uuid4()),
}
post("/api/billing/cancellation", {**cancel, "customerId": "other-customer"}, 400)
post("/api/billing/cancellation", {**cancel, "expectedEffectiveAt": "2000-01-01T00:00:00Z"}, 409)
try:
    post("/api/billing/cancellation", cancel)
    post("/api/billing/cancellation", cancel)
    canceled = state()
    assert datetime.fromisoformat(
        canceled["endingAt"].replace("Z", "+00:00")
    ) == datetime.fromisoformat(preview["effectiveAt"].replace("Z", "+00:00"))
    assert canceled["plan"]["code"] == before["plan"]["code"]
    assert canceled["status"] == "active"
finally:
    if state()["endingAt"]:
        keep = {
            "subscriptionId": external_id,
            "operation": "uncancel",
            "idempotencyKey": str(uuid.uuid4()),
        }
        post("/api/billing/cancellation", keep)
        post("/api/billing/cancellation", keep)
after = state()
assert after["endingAt"] is None
assert after["plan"]["code"] == before["plan"]["code"]
assert after["status"] == "active"
print(
    json.dumps(
        {
            "result": "passed",
            "subscriptionId": external_id,
            "plan": after["plan"]["code"],
            "finalStatus": after["status"],
            "endingAt": after["endingAt"],
            "checks": [
                "authenticated session",
                "tampering rejected",
                "stale quote rejected",
                "cancel persisted",
                "replay",
                "keep persisted",
            ],
        }
    )
)
browser.close()
