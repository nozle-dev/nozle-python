"""AsyncNozle runs the same operations as Nozle: the same requests, results and errors, awaited."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import warnings
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest
import requests_mock as requests_mock_module

from nozle import (
    AsyncNozle,
    Nozle,
    NozleAPIError,
    NozleAuthenticationError,
    NozleTransportError,
    NozleValidationError,
    WebhookVerificationError,
)
from nozle.integrations.capture import capture_async_http_client, wrap_http_client

ENGINE = "https://api.example/engine"
CORE = "https://api.example/core"
# One body that every reader accepts: each operation picks its own key.
UNIVERSAL: Dict[str, Any] = {
    "plans": [],
    "subscriptions": [{"external_id": "sub_1"}],
    "customer": {"external_id": "cust"},
    "customer_usage": {"amount_cents": 1},
    "usage_periods": [{"amount_cents": 2}],
    "credit_systems": [],
    "meta": {},
    "invoice": {"nozle_id": "inv_1", "file_url": "https://files.example/inv.pdf"},
    "alert": {"code": "a1"},
    "manage_payment_method": {
        "url": "https://billing.stripe.com/x",
        "expires_at": None,
        "provider": "stripe",
    },
    "entity": {"name": "User", "metadata": {"seat": 1}},
    "entity_subscription": {"external_entity_id": "user-42"},
    "entity_subscription_checkout": {"items": []},
    "credit_balance_import": {"replayed": False},
    "billing_state_import": {"replayed": True},
}
HEADERS = ("authorization", "content-type", "idempotency-key")
Sent = Tuple[str, str, List[Tuple[str, str]], Any, Dict[str, str]]


def _sent(method: str, url: str, body: bytes, headers: Any) -> Sent:
    parts = urlsplit(url)
    return (
        method,
        f"{parts.scheme}://{parts.netloc}{parts.path}",
        sorted(parse_qsl(parts.query)),
        json.loads(body) if body else None,
        {name: headers[name] for name in HEADERS if name in headers},
    )


def async_client(handler: Callable[[httpx.Request], httpx.Response], **options: Any) -> AsyncNozle:
    return AsyncNozle(
        "sk_test",
        base_url=ENGINE,
        events_url=CORE,
        _client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **options,
    )


ITEMS = [{"external_entity_id": "user-42", "plan_code": "pro"}]
TRANSITION = {"customer_id": "cust", "subscription_id": "sub_1", "operation": "uncancel"}
STAMP = "2026-10-15T10:00:00Z"
CALLS: List[Callable[[Any], Any]] = [
    lambda n: n.track("cust", "api_calls", {"tokens": 1}, transaction_id="t1", timestamp=STAMP),
    lambda n: n.can("cust", "export", {"plan": "pro"}),
    lambda n: n.plans(),
    lambda n: n.ping(),
    lambda n: n.checkout("cust", "pro", "https://app.example/b", idempotency_key="k1"),
    lambda n: n.checkout_invoice("inv_1", register_mandate=True),
    lambda n: n.manage_payment_method("cust", "https://app.example/b", idempotency_key="m1"),
    lambda n: n.checkout_status("chk_1", customer_id="cust", subscription_id="sub_1"),
    lambda n: n.verify_checkout(
        "chk_1",
        {"razorpay_order_id": "o", "razorpay_payment_id": "p", "razorpay_signature": "s"},
    ),
    lambda n: n.subscribe("cust", "pro"),
    lambda n: n.cancel_subscription("cust", "sub_1", "immediate"),
    lambda n: n.subscription_options("cust", "sub_1"),
    lambda n: n.preview_subscription_change("cust", "sub_1", "team"),
    lambda n: n.withdraw_pending_subscription_change("cust", "sub_1", "sub_2", idempotency_key="w"),
    lambda n: n.preview_subscription_transition(TRANSITION),
    lambda n: n.apply_subscription_transition(TRANSITION, idempotency_key="t"),
    lambda n: n.check_and_deduct("cust", credits=5),
    lambda n: n.current_usage("cust"),
    lambda n: n.past_usage("cust", "sub_1", periods=2),
    lambda n: n.usage.check("cust", "emails", occurred_at=STAMP),
    lambda n: n.usage.track("cust", "emails", idempotency_key="u1", timestamp=STAMP),
    lambda n: n.customers.upsert("cust", name="Acme", email=None),
    lambda n: n.credits.get_balance("cust", "ai"),
    lambda n: n.credits.list_balances("cust"),
    lambda n: n.credits.list_operations("cust", limit=10),
    lambda n: n.credits.get_entity_balance("cust", "user-42", "ai"),
    lambda n: n.credits.list_entity_balances("cust", "user-42"),
    lambda n: n.credits.list_entity_operations("cust", "user-42", cursor="c"),
    lambda n: n.credits.allocate(
        "cust", "user-42", credit_system_code="ai", amount="10", idempotency_key="a1"
    ),
    lambda n: n.credits.deallocate(
        "cust", "user-42", credit_system_code="ai", amount="1", idempotency_key="d1"
    ),
    lambda n: n.credit_systems.list(),
    lambda n: n.invoices.list("cust", payment_overdue=True, per_page=5),
    lambda n: n.invoices.get("inv_1"),
    lambda n: n.invoices.download_url("inv_1"),
    lambda n: n.alerts.list(subscription_id="sub_1"),
    lambda n: n.alerts.get("a1", customer_id="cust", wallet_code="w"),
    lambda n: n.alerts.create("usage_amount", "a1", [{"value": 10}], subscription_id="sub_1"),
    lambda n: n.alerts.update("a1", name="A", subscription_id="sub_1"),
    lambda n: n.alerts.delete("a1", subscription_id="sub_1"),
    lambda n: n.cost_events.track(
        cost_meter_code="llm", cost_event_id="ce1", external_customer_id="cust"
    ),
    lambda n: n.entities.get("cust", "user-42"),
    lambda n: n.entities.list("cust", status="active"),
    lambda n: n.entities.upsert("cust", "user-42", status="active", idempotency_key="e1"),
    lambda n: n.entities.suspend("cust", "user-42", idempotency_key="e2"),
    lambda n: n.entities.activate("cust", "user-42", idempotency_key="e3"),
    lambda n: n.entities.bulk_upsert(
        "cust", [{"external_id": "user-42", "status": "active"}], idempotency_key="b"
    ),
    lambda n: n.entity_subscriptions.ensure("cust", "user-42"),
    lambda n: n.entity_subscriptions.get("cust", "user-42"),
    lambda n: n.entity_subscriptions.list("cust"),
    lambda n: n.entity_subscriptions.checkout("cust", "user-42", plan_code="pro"),
    lambda n: n.entity_subscriptions.change_plan("cust", "user-42", plan_code="team"),
    lambda n: n.entity_subscriptions.checkout_many("cust", items=ITEMS, idempotency_key="b1"),
    lambda n: n.entity_subscriptions.cancel("cust", "user-42", idempotency_key="c1"),
    lambda n: n.entity_subscriptions.remove("cust", "user-42"),
    lambda n: n.margin.summary(start_date="2026-10-01", end_date=None),
    lambda n: n.margin.by_customer(),
    lambda n: n.margin.by_metric(),
    lambda n: n.margin.by_plan(),
    lambda n: n.margin.by_model(),
    lambda n: n.margin.trend("week"),
    lambda n: n.subscribe("cust", "pro", plan_version=1),
    lambda n: n.plan_versions.list("pro"),
    lambda n: n.plan_versions.get("pro", 1),
    lambda n: n.plan_versions.preview_migration("pro", 1),
    lambda n: n.plan_versions.create_migration("pro", 1, to_version=2),
    lambda n: n.plan_versions.list_migrations("pro"),
    lambda n: n.plan_versions.get_migration("pro", "mig_1"),
    lambda n: n.plan_versions.cancel_migration("pro", "mig_1"),
    lambda n: n.can("cust", "export", subscription_id="sub_1", entity_id="ent_1"),
    lambda n: n.margin.forecast(from_date="2026-10-01"),
    lambda n: n.margin.simulate("agent_execution", 1.5, from_date="2026-10-01"),
    lambda n: n.cost_intelligence.report(from_date="2026-10-01", limit=10),
    lambda n: n.cost_intelligence.preview_shared_cost_allocation("pool_1", STAMP),
    lambda n: n.cost_intelligence.allocate_shared_cost("pool_1", STAMP),
    lambda n: n.customers.import_credit_balance(
        "cust",
        {
            "external_subscription_id": "sub_1",
            "credit_system_code": "ai",
            "granted_amount": "5",
            "remaining_amount": "1",
        },
        idempotency_key="i1",
    ),
    lambda n: n.customers.import_billing_state(
        "cust",
        {
            "subscription": {
                "plan_code": "pro",
                "status": "active",
                "billing_authority": "external",
                "external_reference": "ext_1",
                "current_period_start": STAMP,
                "current_period_end": "2026-11-15T10:00:00Z",
            }
        },
        idempotency_key="i2",
    ),
]


@pytest.mark.parametrize("call", CALLS, ids=range(len(CALLS)))
async def test_async_call_sends_the_same_request_and_returns_the_same_result(
    call: Callable[[Any], Any],
) -> None:
    with requests_mock_module.Mocker() as mock:
        mock.register_uri(requests_mock_module.ANY, requests_mock_module.ANY, json=UNIVERSAL)
        expected = call(Nozle("sk_test", base_url=ENGINE, events_url=CORE))
        sync_sent = [
            _sent(r.method, r.url, r.body or b"", {k.lower(): v for k, v in r.headers.items()})
            for r in mock.request_history
        ]

    async_sent: List[Sent] = []

    def handler(request: httpx.Request) -> httpx.Response:
        async_sent.append(_sent(request.method, str(request.url), request.content, request.headers))
        return httpx.Response(200, json=UNIVERSAL)

    async with async_client(handler) as nozle:
        pending = call(nozle)
        assert asyncio.iscoroutine(pending)
        assert await pending == expected
    assert async_sent == sync_sent


async def test_validation_errors_are_raised_when_awaited() -> None:
    nozle = async_client(lambda r: httpx.Response(200, json={}))
    with pytest.raises(NozleValidationError):
        await nozle.track("", "api_calls")
    with pytest.raises(NozleAuthenticationError):
        await AsyncNozle("pk_browser", _client=httpx.AsyncClient()).usage.check("c", "f")
    with pytest.deprecated_call(match="success_url"):
        await nozle.checkout("cust", "pro", success_url="https://merchant.example/done")


async def test_api_errors_keep_status_request_id_and_retry_after() -> None:
    nozle = async_client(
        lambda r: httpx.Response(
            404,
            json={"error": "not_found", "api_key": "secret"},
            headers={"X-Request-Id": "req_1", "Retry-After": "3"},
        )
    )
    with pytest.raises(NozleAPIError) as raised:
        await nozle.plans()
    assert raised.value.status_code == 404
    assert raised.value.request_id == "req_1" and raised.value.retry_after == 3.0
    assert "secret" not in str(raised.value)


async def test_network_failure_is_a_transport_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(NozleTransportError):
        await async_client(fail).ping()


def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> List[float]:
    waits: List[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("nozle._transport.asyncio.sleep", sleep)
    return waits


async def test_reads_retry_with_asyncio_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = _no_sleep(monkeypatch)
    answers: List[Any] = [
        httpx.Response(503, json={}),
        httpx.Response(429, json={}, headers={"Retry-After": "2"}),
        httpx.ConnectError("down"),
        httpx.Response(200, json={"plans": []}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    assert await async_client(handler, max_retries=3).plans() == []
    assert not answers and len(waits) == 3 and waits[1] == 2.0


async def test_writes_retry_only_when_safe_and_zero_disables_and_two_is_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_sleep(monkeypatch)
    calls: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, json={})

    with pytest.raises(NozleAPIError):
        await async_client(handler).plans()
    with pytest.raises(NozleAPIError):
        await async_client(handler, max_retries=0).plans()
    nozle = async_client(handler)
    with pytest.raises(NozleAPIError):
        await nozle.subscribe("cust", "pro")
    with pytest.raises(NozleAPIError):
        await nozle.usage.track("cust", "f", idempotency_key="k", timestamp=STAMP)
    assert (
        calls
        == ["/engine/api/v1/plans"] * 3
        + ["/engine/api/v1/plans", "/engine/api/v1/subscribe"]
        + ["/engine/api/v1/usage/track"] * 3
    )


async def test_usage_track_reuses_its_timestamp_for_the_same_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter(f"2026-10-15T10:00:0{i}.000Z" for i in range(9))
    monkeypatch.setattr("nozle.usage.utc_now_rfc3339_milliseconds", lambda: next(clock))
    stamps: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(json.loads(request.content)["timestamp"])
        return httpx.Response(200, json={})

    nozle = async_client(handler)
    for key in ("k1", "k1", "k2"):
        await nozle.usage.track("cust", "emails", idempotency_key=key)
    assert stamps[0] == stamps[1] != stamps[2]


async def test_track_resolves_and_caches_the_subscription() -> None:
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"subscriptions": [{"external_id": "sub_9"}]})

    nozle = async_client(handler)
    transaction = await nozle.track("cust", "api_calls")
    await nozle.track("cust", "api_calls")
    assert transaction
    assert [r.url.path for r in seen] == [
        "/core/api/v1/subscriptions",
        "/engine/api/v1/events",
        "/engine/api/v1/events",
    ]
    event = json.loads(seen[1].content)["event"]
    assert event["external_subscription_id"] == "sub_9" and event["transaction_id"] == transaction


async def test_track_errors_carry_the_transaction_id_for_a_safe_retry() -> None:
    sent: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content)["event"]["transaction_id"])
        if len(sent) == 1:
            return httpx.Response(502, json={})
        if len(sent) == 2:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, text="accepted")

    nozle = async_client(handler, max_retries=0)
    with pytest.raises(NozleAPIError) as api_error:
        await nozle.track("cust", "api_calls", subscription_id="sub_1")
    generated = api_error.value.transaction_id
    assert generated
    with pytest.raises(NozleTransportError) as network_error:
        await nozle.track("cust", "api_calls", subscription_id="sub_1", transaction_id=generated)
    assert network_error.value.transaction_id == generated
    retried = await nozle.track(
        "cust", "api_calls", subscription_id="sub_1", transaction_id=generated
    )
    assert retried == generated
    assert sent == [generated] * 3


async def test_secret_key_comes_from_the_environment_and_host_sets_both_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOZLE_SECRET_KEY", "sk_env")
    nozle = AsyncNozle(host="https://nozle.internal/", _client=httpx.AsyncClient())
    assert nozle.api_key == "sk_env"
    assert nozle.base_url == "https://nozle.internal/engine"
    assert nozle.events_url == "https://nozle.internal/core"
    monkeypatch.delenv("NOZLE_SECRET_KEY")
    with pytest.raises(ValueError, match="NOZLE_SECRET_KEY"):
        AsyncNozle()


async def test_context_manager_closes_only_its_own_client() -> None:
    async with AsyncNozle("sk_test") as nozle:
        own = nozle._client
    assert own.is_closed
    given = httpx.AsyncClient()
    async with AsyncNozle("sk_test", _client=given):
        pass
    assert not given.is_closed
    await given.aclose()


V = json.loads((Path(__file__).parent / "fixtures" / "webhook_vectors.json").read_text())


async def test_webhook_verify_is_awaited_and_makes_no_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("verify made a request")

    stamp = 1614265330
    secret = V["standard"]["secret"]
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"wh_1.{stamp}.{V['body']}".encode(), hashlib.sha256).digest()
    headers = {
        "webhook-id": "wh_1",
        "webhook-timestamp": str(stamp),
        "webhook-signature": "v1," + base64.b64encode(mac).decode(),
    }
    event = await async_client(handler).webhooks.verify(
        V["body"], headers, webhook_secret=secret, tolerance_seconds=10**9
    )
    assert event.id == "wh_1"
    with pytest.raises(WebhookVerificationError):
        await async_client(handler).webhooks.verify(
            V["body"],
            headers,
            webhook_secret="whsec_" + base64.b64encode(b"z" * 24).decode(),
            tolerance_seconds=10**9,
        )


async def test_capture_records_llm_usage_through_async_nozle() -> None:
    sent: List[httpx.Request] = []
    done = asyncio.Event()

    def nozle_api(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path.endswith("/features/llm_tokens"):
            done.set()
        return httpx.Response(200, json=UNIVERSAL)

    nozle = async_client(nozle_api)
    body = {"id": "c4", "usage": {"prompt_tokens": 5, "completion_tokens": 3}}
    llm = nozle.capture_async_http_client(
        customer_id="cust_1",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)),
    )
    response = await llm.post("https://api.openai.com/v1/chat/completions", json={})
    assert response.json() == body
    await asyncio.wait_for(done.wait(), 2)
    events = [r for r in sent if r.url.path == "/engine/api/v1/events"]
    event = json.loads(events[0].content)["event"]
    assert event["transaction_id"] == "llm-openai-c4"
    assert event["properties"]["input_tokens"] == 5
    with pytest.raises(TypeError):
        wrap_http_client(httpx.Client(), nozle)
    assert capture_async_http_client(nozle) is not None


async def test_record_usage_is_awaited_and_raises_the_first_delivery_error() -> None:
    sent: List[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.path)
        status = 500 if request.url.path.endswith("/cost-events") else 200
        return httpx.Response(status, json=UNIVERSAL)

    nozle = async_client(handler, max_retries=0)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        await nozle.record_usage("cust", model="m", input_tokens=4, output_tokens=2)
    with pytest.raises(NozleAPIError):
        await nozle.record_usage(
            "cust", model="m", input_tokens=4, output_tokens=2, cost_meter_code="llm"
        )
    assert sent[0] == "/core/api/v1/subscriptions"
    assert sent.count("/engine/api/v1/cost-events") == 2
