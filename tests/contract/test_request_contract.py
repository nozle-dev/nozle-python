"""Every request body the SDK sends must fit the API's request schema.

openapi_requests.json (written by scripts/sync_openapi_requests.py from nozle-openapi.yaml)
holds each operation's body schema. The test runs representative SDK calls against a mock,
matches each request to its operation by method and path, and checks the body: no key the schema
does not have, no null where the schema does not allow it, and enum values from the schema.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import pytest
import requests_mock

from nozle import Nozle

OPERATIONS: dict[str, Any] = json.loads((Path(__file__).parent / "openapi_requests.json").read_text())
PATTERNS = [
    (key.split(" ", 1)[0], re.compile("^" + re.sub(r"\{[^/]+\}", "[^/]+", key.split(" ", 1)[1]) + "$"), key)
    for key in OPERATIONS
]
TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list}


def find_operation(method: str, path: str) -> str | None:
    exact = f"{method} {path}"
    if exact in OPERATIONS:
        return exact
    matches = [key for verb, pattern, key in PATTERNS if verb == method and pattern.match(path)]
    return min(matches, key=lambda key: key.count("{")) if matches else None


def problems(value: Any, schema: dict[str, Any], where: str) -> list[str]:
    if not schema:
        return []
    for combinator in ("oneOf", "anyOf"):
        if combinator in schema:
            branches = [problems(value, branch, where) for branch in schema[combinator]]
            return [] if any(not found for found in branches) else min(branches, key=len)
    merged = dict(schema)
    for part in schema.get("allOf", []):
        for key, item in part.items():
            if key == "properties":
                merged.setdefault("properties", {}).update(item)
            elif key == "required":
                merged["required"] = list(merged.get("required", [])) + list(item)
            else:
                merged.setdefault(key, item)
    kinds = merged.get("type")
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    if value is None:
        return [] if "null" in kinds or not kinds else [f"{where}: null is not allowed"]
    if kinds and not any(kind != "null" and isinstance(value, TYPES[kind]) and not (kind in ("integer", "number") and isinstance(value, bool)) for kind in kinds):
        return [f"{where}: {value!r} is not {kinds}"]
    if "enum" in merged and value not in merged["enum"]:
        return [f"{where}: {value!r} is not one of {merged['enum']}"]
    found: list[str] = []
    if isinstance(value, dict) and "properties" in merged:
        extra = merged.get("additionalProperties")
        for key, item in value.items():
            if key in merged["properties"]:
                found += problems(item, merged["properties"][key], f"{where}.{key}")
            elif not extra:
                found.append(f"{where}: unknown key {key!r}")
    if isinstance(value, list) and isinstance(merged.get("items"), dict):
        for index, item in enumerate(value):
            found += problems(item, merged["items"], f"{where}[{index}]")
    return found


def client() -> Nozle:
    return Nozle("sk_test", base_url="https://api.example/engine", events_url="https://api.example/core")


ITEMS = [{"external_entity_id": "user-42", "plan_code": "pro"}]
CALLS: list[Callable[[Nozle], Any]] = [
    lambda n: n.track("cust", "api_calls", {"tokens": 1}, subscription_id="sub_1"),
    lambda n: n.usage.check("cust", "emails"),
    lambda n: n.usage.track("cust", "emails", idempotency_key="k1", timestamp="2026-10-15T10:00:00Z"),
    lambda n: n.cost_events.track(cost_meter_code="llm_tokens", external_customer_id="cust", properties={"tokens": 5}),
    lambda n: n.customers.upsert("cust", name="Acme"),
    lambda n: n.subscribe("cust", "pro"),
    lambda n: n.checkout("cust", "pro", "https://app.example.com/billing"),
    lambda n: n.entity_subscriptions.checkout("cust", "user-42", plan_code="pro"),
    lambda n: n.entity_subscriptions.change_plan("cust", "user-42", plan_code="team"),
    lambda n: n.entity_subscriptions.checkout_many("cust", items=ITEMS, idempotency_key="b1"),
    lambda n: n.entity_subscriptions.cancel("cust", "user-42", idempotency_key="c1"),
    lambda n: n.entities.upsert("cust", "user-42", status="active", idempotency_key="e1"),
    lambda n: n.credits.allocate("cust", "user-42", credit_system_code="ai_credits", amount="10", idempotency_key="a1"),
]


@pytest.mark.parametrize("call", CALLS, ids=range(len(CALLS)))
def test_request_body_fits_the_api_schema(call: Callable[[Nozle], Any]) -> None:
    with requests_mock.Mocker() as mock:
        mock.register_uri(requests_mock.ANY, requests_mock.ANY, json={}, status_code=200)
        try:
            call(client())
        except Exception:  # noqa: BLE001 - only the request matters here, not how the SDK reads {}
            pass
        sent = [request for request in mock.request_history if request.method != "GET"]
    assert sent, "the call sent no request with a body"
    for request in sent:
        path = request.path.split("?", 1)[0]
        key = find_operation(request.method, path)
        assert key, f"{request.method} {path} is not an operation in the spec"
        body = request.json() if request.body else None
        found = problems(body, OPERATIONS[key] or {}, key) if body is not None else []
        assert not found, "\n".join(found)


def test_the_check_rejects_null_unknown_keys_and_values_outside_an_enum() -> None:
    key = find_operation("POST", "/core/api/v1/customers/cust/entity-subscriptions/checkout")
    assert key
    body = {"entity_subscription_checkout": {"items": ITEMS, "billing_time": "", "return_url": None, "extra": 1}}
    found = problems(body, OPERATIONS[key], key)
    assert any("billing_time" in item for item in found)
    assert any("null is not allowed" in item for item in found)
    assert any("unknown key 'extra'" in item for item in found)
