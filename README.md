# Nozle Python SDK

Backend-only Python SDK for Nozle billing, exact-decimal credits, Entity lifecycle,
ledger usage, margin intelligence, and LLM usage capture.

## Install

```bash
pip install nozle-sdk==0.13.0
```

Python 3.9 through 3.13 are supported. The package is typed and includes `py.typed`.

Version `0.13.0` adds `AsyncNozle` for asyncio apps, the `host` option, reading `NOZLE_SECRET_KEY`, opt-in retries, `NozleAPIError.request_id` and `retry_after`, invoices, usage and wallet alerts, `current_usage` and `past_usage`, webhook verification, and LLM usage capture for any httpx client. See the [changelog](CHANGELOG.md).

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

nozle = Nozle(timeout=15)  # reads NOZLE_SECRET_KEY; host defaults to https://api.nozle.app
```

## Async client

`AsyncNozle` has the same constructor options, namespaces and methods as `Nozle`; each
call that reaches the API is awaited. It sends requests with `httpx.AsyncClient`, so
install the `httpx` extra. Validation, errors (`NozleAPIError` with `request_id` and
`retry_after`), retries (2 by default, `max_retries=0` disables) and idempotency rules are the same; retries wait with
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
`await nozle.webhooks.verify(body, headers, webhook_secret=...)` is awaited like every other call
(it makes no request). `nozle.capture_async_http_client(...)` and
`nozle.wrap_http_client(httpx.AsyncClient(...))` record LLM usage through the same event
loop; `AsyncNozle` does not wrap a synchronous `httpx.Client`, use `Nozle` there.

## Public namespaces

The client exposes `events`, `cost_events`, `customers`, `credit_systems`, `credits`,
`entities`, `usage`, and `margin`.

### Webhooks

```python
try:
    event = nozle.webhooks.verify(raw_body, request_headers, webhook_secret="whsec_...")
except nozle.WebhookVerificationError:
    ...  # answer 400
```

Pass the body exactly as received. `webhook_secret` is the
endpoint's `whsec_` secret; pass a list of secrets while one is being rotated. A
`webhook-timestamp` more than 300 s from now fails (`tolerance_seconds=` changes that).

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

- `{"type": "hosted", "url": "https://checkout.stripe.com/...", "checkout_id": "cs_..."}`: the Stripe-hosted Checkout page; redirect the customer to `url`
- `{"type": "razorpay", "key_id": "...", "order_id": "..."}`: Razorpay order for the modal
- `{"type": "processing", "checkout_id": "...", "status": "..."}`: Razorpay order not ready, or a Stripe quoted upgrade replayed while its webhook is pending
- `{"type": "completed", "status": "..."}`
- `{"type": "scheduled", "status": "..."}`

See [Payment providers](#payment-providers-stripe-and-razorpay) for what to do with each.

The deprecated `success_url=` argument remains an alias for `return_url=`. The SDK
never sends both fields and rejects conflicting values.

Backend subscription creation remains available with `nozle.subscribe(customer_id,
plan_code)`. Both checkout and subscription creation require an `sk_` key.

#### Currencies

A plan family can be sold in several currencies, one plan per interval and currency.
With a secret key, `plans()` lists every plan in every currency; `plans(currency="EUR")` lists
only that currency, and `plans(external_customer_id=...)` only the customer's locked currency
(ignored when your organization allows several currencies per customer). A publishable key
without `currency` gets each family's default-currency plans.
A family plan carries `family` (`code`, `currencies`, `default_currency`) and `prices` (every
variant: `currency`, `interval`, `plan_code`, `amount_cents`).

`checkout()` and `subscribe()` take `plan_family_code=`, `interval=` and optional `currency=`
instead of `plan_code`; Core picks the variant (without `currency`: the customer's locked
currency, else the family's default). Needs Core 11.10.0.

```python
nozle.checkout("customer-123", plan_family_code="pro", interval="yearly", currency="EUR",
               return_url="https://merchant.example/billing/complete")
```

### Plan versions

Changing the price of a plan that customers pay for saves it as the plan's next
version: new customers get the new version, and existing subscribers keep the price
they bought until you move them. `plans()` returns current versions only; each plan
has `version` and `is_current`.

```python
# Keep selling the older price to one customer.
nozle.subscribe("cust_123", "pro", plan_version=1)

versions = nozle.plan_versions.list("pro")  # every version, its price and active subscriptions
older = nozle.plan_versions.get("pro", 1)

# Move version 1's subscribers to the current version, each at the end of its billing period.
preview = nozle.plan_versions.preview_migration("pro", 1)
migration = nozle.plan_versions.create_migration("pro", 1)
detail = nozle.plan_versions.get_migration("pro", migration["nozle_id"])  # per-subscription items
nozle.plan_versions.cancel_migration("pro", migration["nozle_id"])  # unschedules moves not started
```

A migration invoices, credits and refunds nothing when it runs: each subscription is
billed the new version from its next period. Subscriptions it cannot move (custom
pricing, a trial the new version would change, a pending plan change, an open checkout,
and so on) are skipped with a `skip_reason`. Sending the same plan code again for an
existing subscription keeps its version; another `plan_version` for it is refused.
`plan_versions` needs a secret key. See
[Plan versioning](https://docs.nozle.dev/guides/billing/plan-versions).

Cancellation is also backend-only. Without a `policy`, the organization's cancellation
timing applies (set in the dashboard; new organizations default to `end_of_period`; existing organizations keep their setting). Pass the timing to
choose it per call:

```python
result = nozle.cancel_subscription("customer-123", "subscription-123", policy="end_of_period")
# result["subscription"]["status"] == "active"
# result["subscription"]["ending_at"] contains the scheduled boundary
```

Settlement options are optional too. Each one left out follows the subscription, then the
organization's cancellation settings:

```python
nozle.cancel_subscription(
    "customer-123",
    "subscription-123",
    policy="immediate",
    credit_action="refund",  # credit, refund, offset or none
    refund_mode="prorated",  # prorated or full
    final_invoice_action="generate",  # generate or skip
)
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

Return the checkout result to the authenticated frontend and redirect the customer to
`checkout["url"]` (Stripe) or open the Razorpay modal. Never expose the secret key.

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

### Importing billing state and opening credit balances

When you move customers from another billing system, import each subscription that system
still bills (Core), then its current credit balance (Engine). Both need a secret key and an
`idempotency_key` (at most 255 bytes): retry an uncertain call with the same key and body.
`"dry_run": True` validates without writing. Failures raise `NozleAPIError`.

```python
imported = nozle.customers.import_billing_state(
    "workspace_123",
    {
        "subscription": {
            "plan_code": "pro_monthly",
            "status": "active",
            "billing_authority": "external",
            "external_reference": "merchant_subscription_123",
            "current_period_start": "2026-10-15T00:00:00Z",
            "current_period_end": "2026-11-15T00:00:00Z",
        }
    },
    idempotency_key="import-workspace_123-state",
)

nozle.customers.import_credit_balance(
    "workspace_123",
    {
        "external_subscription_id": imported["subscription"]["external_subscription_id"],
        "credit_system_code": "ai_credits",
        "granted_amount": "500",
        "remaining_amount": "320",
    },
    idempotency_key="import-workspace_123-credits",
)
```

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

The SDK retries up to 2 times by default (`max_retries=0` disables), and only calls that are safe to
repeat: reads, and calls with an idempotency key or that are idempotent by id. It never retries other
mutation requests. Retry only with the same
idempotency key after deciding the failure is safe to retry.

### Credit notes and refunds

Needs a secret key and a workspace with credit notes enabled (otherwise `NozleAPIError` 403 `feature_unavailable`). A credit note lowers a finalized invoice; its total is settled by crediting the customer's balance, refunding the original payment method (Stripe or Razorpay, started by Nozle), offsetting what the customer still owes, or a mix. `AsyncNozle` has the same methods, awaited.

```python
items = [{"fee_id": "fee_123", "amount_cents": 500}]  # fee_id: fees[].nozle_id of the invoice

# 1. Preview: nothing is created. Taxes and coupons are applied.
preview = nozle.credit_notes.preview("inv_123", items)
preview["max_creditable_amount_cents"]  # the credit note's total
preview["max_refundable_amount_cents"]  # what was paid and not refunded yet

# 2a. Refund the items to the original payment method (partial refund: fewer or smaller items).
note = nozle.credit_notes.refund(
    "inv_123", items, reason="duplicated_charge", metadata={"ticket": "T-42"}
)
note["refund_status"]  # "pending", then "succeeded" or "failed" (webhook credit_note.refund_failure)

# 2b. Or choose the split yourself: the three amounts add up to max_creditable_amount_cents.
nozle.credit_notes.create(
    "inv_123", items, refund_amount_cents=300, credit_amount_cents=200, reason="order_change"
)

nozle.credit_notes.list(customer_id="cus_1", refund_status="failed")
nozle.credit_notes.get(note["nozle_id"])
nozle.credit_notes.void(note["nozle_id"])  # voids the unused credit; never undoes a paid refund
```

`reason` is one of `duplicated_charge`, `product_unsatisfactory`, `order_change`, `order_cancellation`, `fraudulent_charge`, `other` (default). `refund()` raises `NozleAPIError` with code `refund_exceeds_paid_amount` and creates nothing when less was paid than the items are worth; pass `credit_remainder=True` to refund what was paid and credit the rest.

The SDK sends no `Idempotency-Key` with credit note requests yet (Core 11.22.0 accepts one on create), so `create()`, `refund()` and `void()` are never retried, whatever `max_retries` says; `preview()`, `get()` and `list()` are. After a network error or a 5xx on `create()`, call `list(customer_id=..., invoice_number=...)` before trying again. Test errors with `error.has_code(CANNOT_REFUND_UNPAID_INVOICE)` (also `HIGHER_THAN_REMAINING_INVOICE_AMOUNT`, `HIGHER_THAN_REMAINING_FEE_AMOUNT`, `DOES_NOT_MATCH_ITEM_AMOUNTS`, `INVALID_TYPE_OR_STATUS`, `NO_VOIDABLE_AMOUNT`). Nozle stores only the provider's refund id and status, never card or bank details.

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
# A customer with several subscriptions: pick one by external id (or by Entity). Without a selector the
# result is {"allowed": False, "reason": "subscription_required"}.
seats = nozle.can("customer-123", "seats", subscription_id="sub_a")  # or entity_id="ent_1"
deduction = nozle.check_and_deduct("customer-123", credits=5)  # legacy wallet adapter; `feature` is deprecated and ignored
health = nozle.ping()

summary = nozle.margin.summary()
by_customer = nozle.margin.by_customer()
trend = nozle.margin.trend(granularity="week")
forecast = nozle.margin.forecast()  # next 30 days from the last month's daily trend
simulation = nozle.margin.simulate("agent_execution", 1.0, from_date="2026-10-01")

report = nozle.cost_intelligence.report(from_date="2026-10-01T00:00:00Z", limit=50)
preview = nozle.cost_intelligence.preview_shared_cost_allocation(pool_id, "2026-10-01T00:00:00Z")
run = nozle.cost_intelligence.allocate_shared_cost(pool_id, "2026-10-01T00:00:00Z")
```

If `track()` is called without `subscription_id`, the SDK resolves and caches the
customer's single active subscription through Core. `subscribe()`, `checkout()`,
`cancel_subscription()` and `apply_subscription_transition()` clear that cache for the
customer. A change made elsewhere needs an explicit `subscription_id`. `track()` returns the
organization-wide `transaction_id`; when none is supplied, the SDK generates a UUID
version 7 identifier before sending the request. This lets a Cost Event reference the
Feature Event without waiting for a server response, and out-of-order delivery remains
safe.

If `track()` raises after the request was sent (an error response, a network error or a
timeout), the event may still have been stored. The error carries the ID that was sent as
`transaction_id`; retry with it so the event is recorded once. Pass a `timestamp` and reuse
it on the retry too: without one, each attempt is stamped when it arrives.

```python
timestamp = "2026-10-22T12:00:00Z"
try:
    nozle.track("customer-123", "copilot_action", timestamp=timestamp)
except NozleError as error:
    nozle.track(
        "customer-123",
        "copilot_action",
        timestamp=timestamp,
        transaction_id=error.transaction_id,
    )
```

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

### Batching events

`track_batch()` sends many events through Engine's `/events/batch` in one call. It takes the
arguments of `track()` as mappings, splits them into requests, sends them in parallel and
returns one result per event. `AsyncNozle.track_batch()` is the same call, awaited.

```python
result = nozle.track_batch(
    [
        {"customer_id": "cust_123", "event": "api_call", "metadata": {"tokens": 100}},
        {
            "customer_id": "cust_456",
            "event": "api_call",
            "metadata": {"tokens": 40},
            "timestamp": "2026-10-29T09:30:00Z",
        },
    ]
)

print(result.accepted, result.rejected, result.failed)
for r in result.results:
    if r.status == "rejected":
        print(r.index, r.errors)  # {"code": ["value_is_mandatory"]}
    elif r.status == "failed":
        print(r.index, r.transaction_id, r.error)  # resend with this transaction_id
```

The contract:

- **Size.** At most 5000 events per request: the Engine default (`LAGO_EVENTS_BATCH_MAX_LENGTH`).
  `chunk_size` (1 to 5000, default 5000) sets the size of each request, and a longer list is
  split. `concurrency` (1 to 64, default 4) is how many requests are in flight at once (threads
  for `Nozle`, tasks for `AsyncNozle`). Each request body is gzipped; the limit is 16 MiB once
  decoded. Gzip and requests above 100 events need Engine 2.28.0 or later. When an Engine has a
  lower cap it answers `too_many_events` and stores nothing; the SDK then splits that request in
  half and resends each half, down to one event.
- **Result.** `result.results[i]` belongs to `events[i]` and has a `status`: `accepted`
  (stored), `rejected` (Engine refused this event; `errors` maps a field to codes, and
  resending it unchanged fails again) or `failed` (its request failed: `error` is a
  `NozleAPIError`, `NozleTransportError` or another exception, and the event may or may not be
  stored). `accepted`, `rejected`, `failed` and `total` count them. `track_batch()` raises only
  for invalid arguments; a failed request never hides the results of the others.
- **Bad events.** Engine stores the valid events of a request and answers 200 with
  `error_details` naming the refused ones by position: an invalid event, a `transaction_id`
  already stored with other values, or a customer whose payment is overdue
  (`customer_payment_overdue`). The SDK marks those `rejected` and the others `accepted`. When
  Engine stores nothing it answers 422 (or 402 when every event is overdue), still naming the
  bad events. An Engine before 2.28.0 refuses a request as a whole for one invalid event; the
  SDK then marks the named events `rejected` and resends the valid rest (at most 3 requests for
  one chunk), so one bad event costs its own result only.
- **Idempotency.** Every event has a `transaction_id`: yours, or a generated UUID version 7 that
  is also returned in `result.results[i].transaction_id`. Engine records an event once per
  transaction ID, so the SDK retries a request after a network error, timeout, 429 or 5xx
  (`max_retries`, default 2) without storing an event twice. IDs must be unique in one call. To
  retry `failed` events yourself, pass the same `transaction_id` and the same `timestamp`.
- **Timestamps.** Each event keeps its own `timestamp` (RFC 3339 or Unix seconds); without one
  Engine stamps it on arrival. There is no backfill flag: `/events/batch` accepts old and future
  timestamps as sent. It does not apply the 24 hours ahead and 366 days back window that
  `usage.track()` and cost events enforce.
- **Subscription.** Unlike `track()`, `track_batch()` makes no subscription lookup. Pass
  `subscription_id` or let Engine resolve the customer's one active subscription. A customer
  with several or none is a 422 for that event, so it comes back `rejected`.
- **Invalid arguments** (`NozleValidationError`, with the event's `index`, before any request):
  a missing `customer_id` or `event`, a bad `timestamp`, a repeated `transaction_id`, or an
  out-of-range `chunk_size` or `concurrency`.

`track_batch()` is not `usage.track()`: it records events like `track()` does and does not
deduct credits, apply a money-billed feature's checks or return a balance. A list of 0 events
sends nothing. To batch events as they happen, use an event buffer.

### Buffering events

`event_buffer()` returns a buffer that queues events and sends them through `track_batch()` from
a background thread. `buffer.track()` takes the arguments of `track()`, makes no request and
returns the event's `transaction_id` (yours, or a generated UUID version 7 that every resend
keeps, so Engine records the event once). It is available on `Nozle` (sync) only.

```python
def report(error, events):
    # error.code: "buffer_full", "rejected", "failed" or "closed"
    print(error.code, [e["transaction_id"] for e in events], error.errors, error.__cause__)

buffer = nozle.event_buffer(on_error=report)
buffer.track("cust_123", "api_call", {"tokens": 100})

buffer.flush(timeout=10)  # send what is waiting now; False if it did not finish in time
buffer.close()            # flush, stop the thread, refuse later events

with nozle.event_buffer() as buffer:  # or close it with a with block
    buffer.track("cust_123", "api_call")
```

| Option | Default | |
|---|---|---|
| `max_batch_size` | 500 | 1 to 5000. A batch is sent as soon as this many events wait. |
| `flush_interval` | 5 | Seconds between timer flushes of whatever waits; 0 turns the timer off. |
| `max_queue_size` | 10000 | Events waiting plus those in a request being sent. |
| `max_attempts` | 3 | Flushes an event may fail in before it is dropped and reported. |
| `on_error` | warning | `on_error(error, events)`; without it a `NozleTrackingWarning`. |

- **Sending.** One flush runs at a time; a size or timer trigger during a flush waits for it.
  `flush(timeout=None)` and `close(timeout=None)` return True when done, False on timeout.
  `buffer.size` is the number of events waiting.
- **Nothing is lost silently.** Every dropped event goes to `on_error` with an
  `EventBufferError` whose `code` is:
  - `buffer_full`: the queue held `max_queue_size` events; the new event is dropped (`track()`
    still returns its `transaction_id` and raises nothing).
  - `rejected`: Engine refused the event (`error.errors` has its field codes). One report per
    event; it is not resent.
  - `failed`: its request still failed after the batch call's own retries in `max_attempts`
    flushes (`error.__cause__` is the last request error). A failed event goes back to the
    front of the queue for the next flush, with the same `transaction_id`. One report per flush.
  - `closed`: `track()` after `close()`.
- **Backoff.** After a flush in which any event failed, the size and timer flushes pause for
  1 second, doubling after each further flush with failures, up to 60 seconds; a flush without
  failures ends the pause. Events keep queueing meanwhile (up to `max_queue_size`), so an
  outage longer than a few flush intervals does not use up `max_attempts`. `flush()`,
  `close()` and the exit hook send at once, without waiting for the pause.
- **Invalid events** (a missing `customer_id` or `event`, a bad `timestamp`) raise
  `NozleValidationError` from `track()`, before they are queued.
- **Exit.** The worker is a daemon thread. An `atexit` handler flushes with a 5 second timeout
  and reports what it could not send as `failed`; `close()` removes the handler. `close()`
  keeps flushing until the queue is empty, so a failing event is retried up to `max_attempts`.
- **Serverless.** A function host may freeze or end the process right after your handler
  returns, before the timer or the exit hook runs. Call `buffer.flush()` (or `close()`)
  before returning.
- Like `track_batch()`, the buffer makes no subscription lookup, and the queue lives in memory
  only. The OpenAI and Anthropic wrappers still send each event on its own.

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

`wrap_openai` and `wrap_anthropic` return a new client view and never change the
client you pass in, so a server can keep one provider client and wrap it per request
(`wrap_openai(shared_openai, nozle, customer_id=request.team_id)`). Calls on the
original client record nothing.

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

### Workspace suspended or card required (402)

When your Nozle workspace is suspended because its Nozle invoices are unpaid, every API call answers HTTP 402 with `code` `workspace_suspended`. A workspace without a verified card answers 402 with `code` `card_required` the same way. `NozleAPIError` has `status_code` 402 and `code` `workspace_suspended` from `can()`, `track()`, `usage.check()` and every other call (sync and async); `can()` raises instead of returning `allowed: False`. Never retry blindly: the SDK does not retry a 402. Add a card in the Nozle dashboard (`card_required`) or pay the Nozle invoices (`workspace_suspended`) and calls work again at once. Events sent during the suspension are refused, not queued.

```python
try:
    nozle.can("cust_123", "code_completion")
except NozleAPIError as error:
    if error.code == "workspace_suspended":
        ...  # tell your team to pay the Nozle invoices; not allowed, not denied
```

### Payment providers: Stripe and Razorpay

`checkout()` and `checkout_invoice()` use the payment provider connected to your
organization and assigned to the customer, and return a result whose `type` tells you
what to do next. Branch on `type`; a browser callback alone never proves payment.

| `type` | Provider | Next step |
| --- | --- | --- |
| `hosted` | Stripe, Razorpay link | Redirect the customer to `url` (the Stripe Checkout page; `payment_url` is the same address), then confirm as described below (Stripe: `checkout_id` is the Stripe session id, not a status id) |
| `razorpay` | Razorpay | Open the Razorpay modal with `key_id`, `order_id`, `amount_cents` and `currency`, then verify |
| `processing` | Razorpay or Stripe | Recover with `checkout_status(checkout_id)` instead of starting another checkout. Razorpay returns it while the order is not ready; Stripe returns it when a quoted upgrade is replayed while its webhook is still pending |
| `completed`, `scheduled` | Either | No payment is due now: credits covered it, or the change takes effect later |

**Stripe.** Every Stripe checkout is the Stripe-hosted Checkout page; there is no embedded
form. `checkout_id` of a Stripe result is the Stripe session id (`cs_...`), which
`checkout_status` does not accept. An upgrade session lasts 35 minutes, an Entity batch 23 hours. Redirect the customer to `url`. Pass `cancel_url=` to choose
where Stripe sends a customer who leaves the page (validated like `return_url`, defaults to
it). A return URL may contain the literal `{CHECKOUT_SESSION_ID}`, which Stripe replaces with
the Checkout Session id. Credit top-up purchases also return a Stripe-hosted `payment_url`
(valid for 24 hours).

```python
result = nozle.checkout(
    "customer-123",
    "pro_monthly",
    return_url="https://app.example.com/settings/billing",
    cancel_url="https://app.example.com/pricing",
)
if result["type"] == "hosted":
    # Redirect the customer to result["url"].
    # Paid upgrade (result["plan_upgrade_intent_id"]): after they return, poll
    #   nozle.checkout_status(intent_id, customer_id=..., subscription_id=...);
    #   status is awaiting_payment (hosted nested), processing, succeeded or expired.
    # New subscription or invoice (result["invoice_id"]): there is no status call. The customer
    #   returns to your return URL; confirm with the invoice.payment_succeeded or subscription
    #   webhook, or by reloading billing state (can() or the subscription read).
    ...
```

A new-subscription checkout retried while the first attempt is unpaid reopens payment
for the same invoice when the plan is the same (or returns `completed` if it was paid
meanwhile). For another plan, the unpaid attempt is expired and replaced, or the request
gets HTTP 409 `subscription_checkout_in_progress`
(`entity_subscription_checkout_in_progress` for an Entity checkout) when it cannot be
expired yet. Keep the body identical for one idempotency key: a different request with
the same key gets HTTP 409 `idempotency_key_reused`, including an invoice checkout.
Checkout error bodies carry `status`, `error` and `code`.

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

**Manage payment method (Stripe only).** `manage_payment_method(customer_id, return_url)`
returns `{"url", "expires_at", "provider": "stripe"}`; redirect the customer to `url` to add
or replace their saved card. Razorpay customers pay each invoice by payment link, so for
them it raises `NozleAPIError` (HTTP 422) where `error.has_code("razorpay_pays_by_link")`
is true.

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
