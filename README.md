# Nozle Python SDK

Feature Event writes use the Go Engine at `https://api.nozle.app/engine/api/v1/events` (batch: `/events/batch`). Configure `base_url` for a custom ingestion endpoint. `events_url` configures Core billing resources and subscription discovery.

Backend-only Python SDK for Nozle billing, exact-decimal credits, Entity lifecycle,
ledger usage, margin intelligence, and LLM usage capture.

## Install

```bash
pip install nozle-sdk==0.10.0
```

Python 3.9 through 3.13 are supported. The package is typed and includes `py.typed`.

Version `0.9.1` converts RFC 3339 Feature Event timestamps to the Unix-second format Core accepts, including events created by the OpenAI and Anthropic wrappers. See the [changelog](CHANGELOG.md).

## Authentication

- `pk_` publishable keys may call only `plans()`.
- `sk_` secret keys are required for every customer read, mutation, event, credit,
  Entity, usage, and margin operation.
- Protected operations reject `pk_` locally before network I/O.

Keep secret keys on a trusted backend. This package is not a browser SDK.
When a request originates in a browser, authenticate it in your backend and derive the
Nozle `customer_id` from that authenticated user, team, or organization. Do not treat a
browser-supplied customer ID as authoritative.

```python
from nozle import Nozle

nozle = Nozle(
    api_key="sk_backend_...",
    base_url="https://api.nozle.app/engine",
    events_url="https://api.nozle.app/core",
    timeout=15,
)
```

## Async client

`AsyncNozle` has the same constructor options, namespaces and methods as `Nozle`; each
call that reaches the API is awaited. It sends requests with `httpx.AsyncClient`, so
install the `httpx` extra. Validation, errors (`NozleAPIError` with `request_id` and
`retry_after`), opt-in retries and idempotency rules are the same; retries wait with
`asyncio.sleep`, so they never block the event loop.

```bash
pip install "nozle-sdk[httpx]"
```

```python
from nozle import AsyncNozle

async with AsyncNozle(max_retries=2) as nozle:  # reads NOZLE_SECRET_KEY
    allowed = await nozle.can("customer-123", "export")
    await nozle.usage.track("customer-123", "emails", idempotency_key=message_id)
    invoices = await nozle.invoices.list("customer-123")
```

Without `async with`, call `await nozle.aclose()` when you are done. `nozle.events.create_transaction_id()`
and `nozle.cost_events.create_cost_event_id()` make no request and are not awaited.
`await nozle.webhooks.verify(body, headers)` is awaited because a `jwt` endpoint reads
Nozle's public key once. `nozle.capture_async_http_client(...)` and
`nozle.wrap_http_client(httpx.AsyncClient(...))` record LLM usage through the same event
loop; `AsyncNozle` does not wrap a synchronous `httpx.Client`, use `Nozle` there.

## Public namespaces

The client exposes `events`, `cost_events`, `customers`, `credit_systems`, `credits`,
`entities`, `usage`, and `margin`.

### Plans and checkout

```python
plans = nozle.plans()

checkout = nozle.checkout(
    "customer-123",
    "pro",
    return_url="https://merchant.example/billing/complete",
)
```

Checkout can return any production transition shape:

- `{"type": "stripe", "client_secret": "cs_..."}`: Stripe embedded Checkout Session
- `{"type": "razorpay", "key_id": "...", "order_id": "..."}`: Razorpay order for the modal
- `{"type": "hosted", "payment_url": "https://..."}`: declared in the result types; `POST /checkout` does not return it for Razorpay (invoice payment URLs and credit top-ups return a `payment_url` with no checkout id to poll)
- `{"type": "processing", "checkout_id": "...", "status": "..."}`: Razorpay order not ready, or a Stripe quoted upgrade replayed while its webhook is pending
- `{"type": "completed", "status": "..."}`
- `{"type": "scheduled", "status": "..."}`

See [Payment providers](#payment-providers-stripe-and-razorpay) for what to do with each.

The deprecated `success_url=` argument remains an alias for `return_url=`. The SDK
never sends both fields and rejects conflicting values.

Backend subscription creation remains available with `nozle.subscribe(customer_id,
plan_code)`. Both checkout and subscription creation require an `sk_` key.

Cancellation is also backend-only. It defaults to end-of-period behavior, preserving
access until Nozle's authoritative billing boundary:

```python
result = nozle.cancel_subscription("customer-123", "subscription-123")
# result["subscription"]["status"] == "active"
# result["subscription"]["ending_at"] contains the scheduled boundary
```

Immediate termination must be explicit:

```python
nozle.cancel_subscription("customer-123", "subscription-123", policy="immediate")
```

Create one payment for a mixed basket of Entity plans:

```python
checkout = nozle.entity_subscriptions.checkout_many(
    "workspace_123",
    billing_time="anniversary",
    return_url="https://app.example.com/settings/billing",
    idempotency_key="workspace-123-seat-purchase-v1",
    items=[
        {"external_entity_id": "seat_pro_001", "plan_code": "pro_monthly"},
        {"external_entity_id": "seat_max_001", "plan_code": "max_monthly"},
    ],
)
```

Return the checkout result to the authenticated frontend and mount
`checkout["client_secret"]` using `@nozle-js/react`. Never expose the secret key.

### Customers and Credit Systems

Customer writes go directly to Nozle Core using the merchant's secret key, so
Core derives the owning organization from that key.

```python
customer = nozle.customers.upsert(
    "customer-123",
    name="Acme",
    email="billing@example.com",
)

# Link a customer that already exists in Stripe, without creating a new one.
# A provider assignment needs provider_customer_id or sync_with_provider=True.
nozle.customers.upsert(
    "customer-123",
    billing_configuration={
        "payment_provider": "stripe",
        "payment_provider_code": "stripe_main",
        "provider_customer_id": "cus_ABC123",
    },
)

# Follows every active Core page and returns one normalized list.
systems = nozle.credit_systems.list()
```

If you create customers through the Nozle REST API instead of this SDK, a customer needs
`sync_with_provider: true` or a `provider_customer_id`; otherwise its payment provider
assignment is dropped.

### Customer and Entity credits

```python
balance = nozle.credits.get_balance("customer-123", "ai_credits")
balances = nozle.credits.list_balances("customer-123")
operations = nozle.credits.list_operations(
    "customer-123",
    credit_system_code="ai_credits",
    limit=50,
    cursor=None,
)

entity_balance = nozle.credits.get_entity_balance(
    "customer-123", "user-42", "ai_credits"
)
entity_balances = nozle.credits.list_entity_balances("customer-123", "user-42")
entity_operations = nozle.credits.list_entity_operations(
    "customer-123", "user-42", limit=50
)

allocation = nozle.credits.allocate(
    "customer-123",
    "user-42",
    credit_system_code="ai_credits",
    amount="100.000000000001",
    idempotency_key="allocation-user-42-v1",
)
```

Every credit amount is returned as a string and is never converted to `float`.
Allocation and deallocation amounts must be positive decimal strings with at most 12
decimal places. Mutation idempotency keys are mandatory and must fit within 255 UTF-8
bytes.

### Entities

```python
entity = nozle.entities.get("customer-123", "user-42")
page = nozle.entities.list("customer-123", status="active", limit=50)

result = nozle.entities.upsert(
    "customer-123",
    "user-42",
    status="active",
    name="Asha",
    metadata={"team": "support"},
    idempotency_key="entity-user-42-v1",
)

nozle.entities.suspend(
    "customer-123", "user-42", idempotency_key="suspend-user-42-v1"
)
nozle.entities.activate(
    "customer-123", "user-42", idempotency_key="activate-user-42-v1"
)

nozle.entities.bulk_upsert(
    "customer-123",
    [
        {"external_id": "user-42", "status": "active"},
        {"external_id": "user-43", "status": "suspended"},
    ],
    idempotency_key="entity-import-v1",
)
```

Entity IDs use UTF-8 byte limits, list limits are 1–100, and bulk upserts accept 1–500
unique Entity IDs.

### Ledger usage

`usage.check()` is advisory and does not mutate a balance. `usage.track()` atomically
records consumption and requires an idempotency key.

```python
preview = nozle.usage.check(
    "customer-123",
    "agent_execution",
    entity_id="user-42",
    credit_system_code="ai_credits",
    properties={"model": "gpt-5"},
    occurred_at="2026-07-20T12:00:00.750Z",
)

consumption = nozle.usage.track(
    "customer-123",
    "agent_execution",
    entity_id="user-42",
    properties={"model": "gpt-5"},
    timestamp="2026-07-20T12:00:00.750Z",
    idempotency_key="execution-123",
)
```

The SDK does not automatically retry mutation requests. Retry only with the same
idempotency key after deciding the failure is safe to retry.

## Existing operations

```python
transaction_id = nozle.track(
    "customer-123",
    "copilot_action",
    metadata={"mode": "planning"},
    subscription_id="subscription-123",
)

nozle.cost_events.track(
    cost_meter_code="ai_tokens",
    parent_transaction_id=transaction_id,
    request_id="provider-request-123",
    operation_key="planning",
    properties={"model": "gpt-5", "type": "output", "tokens": 900},
)

entitlement = nozle.can("customer-123", "code_completion")
deduction = nozle.check_and_deduct("customer-123", credits=5)  # legacy wallet adapter; `feature` is deprecated and ignored
health = nozle.ping()

summary = nozle.margin.summary()
by_customer = nozle.margin.by_customer()
trend = nozle.margin.trend(granularity="week")
```

If `track()` is called without `subscription_id`, the SDK resolves and caches the
customer's single active subscription through Core. `subscribe()`, `checkout()`,
`cancel_subscription()` and `apply_subscription_transition()` clear that cache for the
customer. A change made elsewhere needs an explicit `subscription_id`. `track()` returns the
organization-wide `transaction_id`; when none is supplied, the SDK generates a UUID
version 7 identifier before sending the request. This lets a Cost Event reference the
Feature Event without waiting for a server response, and out-of-order delivery remains
safe.

You can generate both identifiers explicitly when coordinating asynchronous work:

```python
transaction_id = nozle.events.create_transaction_id()
cost_event_id = nozle.cost_events.create_cost_event_id()

nozle.track(
    "customer-123",
    "copilot_action",
    subscription_id="subscription-123",
    transaction_id=transaction_id,
)

nozle.cost_events.track(
    cost_event_id=cost_event_id,
    cost_meter_code="ai_tokens",
    parent_transaction_id=transaction_id,
    properties={"model": "gpt-5", "type": "input", "tokens": 150},
)
```

For a cost attributable to a customer but not one Feature Event, provide
`external_customer_id` instead of `parent_transaction_id`. Cost Event methods require a
secret key.

## OpenAI and Anthropic wrappers

Install an optional provider dependency:

```bash
pip install "nozle-sdk[openai]"
pip install "nozle-sdk[anthropic]"
```

```python
from openai import OpenAI
from nozle import Nozle, wrap_openai

nozle = Nozle("sk_backend_...")
openai = wrap_openai(
    OpenAI(),
    nozle,
    customer_id="customer-123",
    metric_code="llm_tokens",
    cost_meter_code="ai_tokens",
    feature="assistant",
)

response = openai.chat.completions.create(
    model="gpt-5",
    messages=[{"role": "user", "content": "Hello"}],
)
```

Synchronous clients, asynchronous clients, synchronous streams, and asynchronous
streams are supported for OpenAI and Anthropic. The parent Feature Event contains:

- `model`
- `provider`
- `input_tokens`
- `output_tokens`
- `latency_ms`
- optional `feature`

When `cost_meter_code` is set, the wrapper generates the parent `transaction_id`
before delivery and emits one linked Cost Event for each non-zero token category.
The canonical Cost Event properties are `tokens`, `provider`, `model`, and `type`.
OpenAI supports `input`, `cached_input`, `output`, and `reasoning`; Anthropic supports
`input`, `cached_input`, `cache_write`, and `output`. Parent and child events may
arrive out of order safely.

Provider failures are propagated. If the provider succeeds but Nozle tracking fails,
the successful provider response or completed stream is preserved and a
`NozleTrackingWarning` is emitted without credential-bearing error details.

## Errors

`NozleAPIError` exposes `operation`, `status_code`, credential-safe
`response_details`, the top-level `code`, and `has_code()` (see
[Payment providers](#payment-providers-stripe-and-razorpay)). Local contract failures use `NozleValidationError` or
`NozleAuthenticationError`; transport failures use `NozleTransportError`. Exception
messages redact API keys and secret-shaped response fields.

### Payment providers: Stripe and Razorpay

`checkout()` and `checkout_invoice()` use the payment provider connected to your
organization and assigned to the customer, and return a result whose `type` tells you
what to do next. Branch on `type`; a browser callback alone never proves payment.

| `type` | Provider | Next step |
| --- | --- | --- |
| `stripe` | Stripe | Send `client_secret` (a `cs_` embedded Checkout Session secret) to the browser |
| `razorpay` | Razorpay | Open the Razorpay modal with `key_id`, `order_id`, `amount_cents` and `currency`, then verify |
| `hosted` | Razorpay | Declared in the result types; `POST /checkout` does not return it for Razorpay. Invoice payment URLs and credit top-ups return a `payment_url` (an rzp.io payment link) with no checkout id to poll |
| `processing` | Razorpay or Stripe | Recover with `checkout_status(checkout_id)` instead of starting another checkout. Razorpay returns it while the order is not ready; Stripe returns it when a quoted upgrade is replayed while its webhook is still pending |
| `completed`, `scheduled` | Either | No payment is due now: credits covered it, or the change takes effect later |

**Stripe.** Plan and invoice checkout always create an embedded Checkout Session. They
never return a Stripe-hosted URL or your Stripe publishable key, so the browser needs the
`pk_` key for the Stripe account connected in Nozle (the React `Checkout` component takes
it as `publishableKey`). Credit top-up purchases are different: they return a
Stripe-hosted `payment_url` (`checkout.stripe.com`, valid for 24 hours).

```python
result = nozle.checkout(
    "customer-123",
    "pro_monthly",
    return_url="https://app.example.com/settings/billing",
)
if result["type"] == "stripe":
    # Return the secret to the browser and mount Stripe Embedded Checkout with it.
    client_secret = result.get("client_secret") or result.get("clientSecret")
    # A new subscription has no checkout ID to poll: reload the subscription after
    # Embedded Checkout completes. A paid upgrade carries plan_upgrade_intent_id.
```

Idempotency keys have two Stripe edge cases today. A new-subscription checkout retried
while the subscription is still `incomplete` fails with HTTP 422 `validation_errors`
containing `subscription_incomplete`, even with the same key; recover by calling
`checkout_invoice()` with the `invoice_id` from the first response. An invoice checkout
retried with the same key and a different body currently returns HTTP 500, not 409, so
keep the body identical for one key.

**Razorpay.** Razorpay supports only INR. Return the `razorpay` result to the browser,
open the modal there, and post the handler's three fields back to your backend.
`verify_checkout()` sends them to Nozle, which checks the signature and fetches the
payment from Razorpay; the SDK does not check the signature locally. Verify and status
need an `sk_` key.

```python
# Backend: create. The result is {"type": "razorpay", "checkout_id", "key_id", "order_id",
# "amount_cents" (paise), "currency": "INR", "expires_at", "invoice_id" or
# "plan_upgrade_intent_id"}.
checkout = nozle.checkout(
    "customer-123",
    "pro_monthly",
    return_url="https://app.example.com/settings/billing",
    idempotency_key="checkout-customer-123-pro_monthly-v1",
    # register_mandate=True,  # UPI AutoPay: the result also has customer_id,
    #                         # recurring and mandate_max_amount_cents
)
# Invoice-based collection: nozle.checkout_invoice(invoice_id, idempotency_key=attempt_id)

# Browser (after loading https://checkout.razorpay.com/v1/checkout.js):
#   new Razorpay({key: key_id, order_id, amount: amount_cents, currency, handler}).open()
# For a mandate also pass customer_id and recurring: "1".

# Backend: verify what the handler returned, then poll until both statuses succeed.
if checkout["type"] == "razorpay":
    status = nozle.verify_checkout(
        checkout["checkout_id"],
        {
            "razorpay_order_id": callback["razorpay_order_id"],
            "razorpay_payment_id": callback["razorpay_payment_id"],
            "razorpay_signature": callback["razorpay_signature"],
        },
        customer_id="customer-123",
        subscription_id="subscription-123",
    )
    status = nozle.checkout_status(
        checkout["checkout_id"],
        customer_id="customer-123",
        subscription_id="subscription-123",
    )
    # status["status"]: awaiting_payment | processing | succeeded | failed | expired | needs_review
```

Authenticate the buyer and bind each `checkout_id` to that buyer before forwarding browser
callbacks. Pass both scope fields to tie a call to one customer and subscription. Display
success only when `status` and `fulfillment_status` are both `succeeded`; a browser callback
or a pending debit is not proof that credits or subscriptions are available. Reuse your
idempotency key when retrying creation. UPI AutoPay requires merchant enablement and
explicit customer authorization; one-time card or UPI payments do not create a mandate.

Provider failures raise `NozleAPIError`. `error.code` is the top-level code, and
`error.has_code()` also matches the codes listed in `error_details`, for example
`razorpay_requires_inr`, `payment_provider_required` or `invalid_checkout_signature`.

```python
from nozle import NozleAPIError

try:
    nozle.checkout("customer-123", "pro_monthly", return_url=return_url)
except NozleAPIError as error:
    if error.has_code("razorpay_requires_inr"):
        ...  # price the plan in INR for customers who pay through Razorpay
    else:
        raise
```

See the [Stripe](https://docs.nozle.dev/integrations/stripe) and
[Razorpay](https://docs.nozle.dev/integrations/razorpay) integration guides for connection
setup, webhooks, and limits.

## React billing portal subscription management

See the [runnable authenticated merchant integration](examples/billing_portal/README.md) for Cancel, Keep, payment-backed upgrades, scheduled downgrades, and exact pending-change withdrawal through the React billing portal. It reuses the existing transition methods and derives customer identity from the merchant session. For end-of-period cancellation, optional `expected_effective_at` preserves the exact preview timestamp and rejects a changed date atomically on compatible Engine/Core deployments. Existing calls without this option remain compatible.


Version `0.9.0` adds `subscription_options(customer_id, subscription_id)`,
`preview_subscription_change(customer_id, subscription_id, plan_code)`, and
`withdraw_pending_subscription_change(customer_id, subscription_id, pending_subscription_id, idempotency_key=...)`.
Use the existing checkout/transition methods with the selected subscription, signed quote,
and a stable action key. Scope `checkout_status` and `verify_checkout` with both
`customer_id` and `subscription_id`. See the [method examples](https://docs.nozle.dev/sdks/python/billing#subscription-self-service).

## License

MIT. Versions up to 0.9.1 were published under AGPL-3.0-or-later.
