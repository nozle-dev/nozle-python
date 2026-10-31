# Changelog

## Unreleased

## 0.27.0 — 2026-10-31

### Added

- `usage.holds` on `Nozle` and `AsyncNozle` reserves credits before work whose cost is known only after it (Engine credit balance holds, NLD-9). Needs Engine 2.30.0 and a secret key.
  - `create(customer_id, feature_code, *, idempotency_key, entity_id=None, credit_system_code=None, properties=None, timestamp=None, ttl_seconds=None)` sends `POST /usage/holds` with the `usage.track()` body plus `ttl_seconds` (Engine default 900, 1 to 86400). The `Idempotency-Key` is required, and a key retried from the same client sends the same generated `timestamp`, as `usage.track()` does. A denial is a result, not an error: `allowed: False`, `status` `denied`, `reason` `insufficient_credits`.
  - `finalize(hold_id, *, properties=None, timestamp=None)` spends the actual usage (`POST /usage/holds/{id}/finalize`) and returns the `usage.track()` result plus `hold_id`, `hold_status`, `credits_held`, `credits_released` and `hold_expired`. Engine replays a finalized hold for the same body, so it is retried, and the generated `timestamp` is kept per hold.
  - `release(hold_id)` (idempotent; HTTP 409 `hold_already_resolved` for a finalized hold), `get(hold_id)` and `list(customer_id, *, status=None)` (`GET /customers/{id}/credit-holds`, the newest 100).
  - Engine errors keep their code on `NozleAPIError`: `hold_not_found` (404), `hold_already_resolved` (409), `hold_ttl_invalid` (400). An invalid `ttl_seconds` or `status` raises `NozleValidationError` before any request.
  - Types: `CreditHold`, `CreditHoldResult`, `CreditHoldFinalizeResult`, `CreditHoldList`, `CreditHoldStatus`, and `UsageHoldsNamespace`.
- Balances gain `held`, the credits reserved by open holds and already left out of `available`: `CreditBalance.held`, `CreditBalanceSource.held`, and `EntityCreditBalance.held`, `entity_held` and `shared_held`. Engine before 2.30.0 does not send them.

## 0.26.0 — 2026-10-31

### Added

- `Nozle.event_buffer(max_batch_size=500, flush_interval=5, max_queue_size=10000, max_attempts=3, on_error=None)` returns an `EventBuffer` that queues events and sends them through `track_batch()` from a daemon thread: when `max_batch_size` events wait, every `flush_interval` seconds, on `flush(timeout=None)`, on `close(timeout=None)` (also a context manager) and at process exit (an `atexit` handler with a 5 second timeout, removed by `close()`). `buffer.track()` takes the arguments of `track()`, makes no request and returns the `transaction_id` (generated as UUID v7 when missing and kept on every resend). Dropped events go to `on_error(error, events)` with an `EventBufferError` whose `code` is `buffer_full` (the queue is full; the new event is dropped), `rejected` (Engine refused it; not resent), `failed` (still failing after `max_attempts` flushes; after a flush with failures, size and timer flushes pause 1 s, doubling per consecutive failing flush up to 60 s, reset by a flush without failures, while `flush()`, `close()` and the exit hook do not wait) or `closed` (`track()` after `close()`); without a callback, a `NozleTrackingWarning`. The same names, defaults and codes as the Node and Go SDKs. Serverless code must call `flush()` or `close()` before returning. `AsyncNozle` has no buffer yet.
- A batch call with `concurrency=1` (or one chunk) runs on the calling thread instead of a thread pool, so it also works during interpreter exit.

### Known

- The OpenAI and Anthropic wrappers and `capture_http_client()` still send each event on its own, not through a buffer.
- The queue is kept in memory only.

## 0.25.0 — 2026-10-31

### Added

- `credit_notes` on `Nozle` and `AsyncNozle`: `preview()` (Core `POST /credit_notes/estimate`), `create()` with `credit_amount_cents`, `refund_amount_cents`, `offset_amount_cents`, `reason`, `description` and `metadata`, `refund()` (preview, then a credit note refunded to the original payment method; `credit_remainder` for partial payment), `list()`, `get()` and `void()`. Typed `CreditNote`, `CreditNotePreview`, `CreditNotePage`, `CreditNoteItem`, `CreditNoteReason`, and the error-code constants (`REFUND_EXCEEDS_PAID_AMOUNT`, `HIGHER_THAN_REMAINING_INVOICE_AMOUNT`, ...). `create()`, `refund()` and `void()` are never retried because they send no `Idempotency-Key` yet; `preview()`, `get()` and `list()` follow `max_retries`. Needs a secret key and a workspace with credit notes enabled.

## 0.24.0 — 2026-10-31

### Added

- `track_batch(events, chunk_size=None, concurrency=None)` on `Nozle` and `AsyncNozle` sends many events through `/events/batch`: gzipped chunks of at most 5000 (the Engine 2.28.0 maximum), a bounded number of requests in flight, and one result per event (`accepted`, `rejected` with Engine's per-field codes, or `failed` with the error of its request). Engine 2.28.0 stores the valid events of a request and names the refused ones on a 200. A request an older Engine refuses whole is not lost: the SDK rejects the events it names and resends the rest, and a request above a lower Engine cap (`too_many_events`) is split in half and resent. Gzip and chunks above 100 need Engine 2.28.0+. Every event has a transaction ID (generated and returned if missing), so a retried request records each event once. Raises `NozleValidationError` (now with an `index` for one event's fields) for invalid arguments before any request. It makes no subscription lookup; Engine resolves it. New: `TrackBatchEvent`, `TrackBatchResult`, `TrackBatchEventResult`.

## 0.23.0 — 2026-10-30

### Breaking

- `webhooks.verify(body, headers, *, webhook_secret, tolerance_seconds=300)` verifies the Standard Webhooks signature (`webhook-id`, `webhook-timestamp`, `webhook-signature`) and is the only scheme. `webhook_secret` is the endpoint's `whsec_` secret, or a list of them while rotating; a timestamp more than `tolerance_seconds` from now fails. It makes no request (still awaited on `AsyncNozle`). The `hmac_key` option, `X-Nozle-Signature*` headers and the public-key read are removed. `WebhookVerificationError.code` is `missing_signature`, `missing_secret`, `invalid_timestamp` (non-numeric or outside the tolerance), `invalid_signature` or `invalid_payload`, the same codes as the Node and Go SDKs; `NozleWebhookEvent.id` is `webhook-id`.

## 0.22.0 — 2026-10-29

### Changed

- `Nozle` and `AsyncNozle` retry up to 2 times by default (`max_retries` was 0), for network errors, timeouts, 429 and 5xx on calls that are safe to repeat only (reads, calls with an `Idempotency-Key`, idempotent-by-id calls). Other mutations are never retried. `max_retries=0` disables retries.

## 0.21.0 — 2026-10-29

Needs Core 11.10.0 (one plan family sold in several currencies).

### Added

- `plans(currency=None, external_customer_id=None)` sends `currency` and `external_customer_id`. With a secret key and neither, every plan is listed in every currency; `currency` keeps that currency, and `external_customer_id` the customer's locked currency (ignored when the organization allows several currencies per customer). A publishable key without `currency` gets each family's default-currency plans. `Plan` gains `family` (`PlanFamilyRef`: `code`, `currencies`, `default_currency`) and `prices` (`list[PlanPrice]`: `currency`, `interval`, `plan_code`, `amount_cents`).
- `checkout()` and `subscribe()` accept `plan_family_code=`, `interval=` and `currency=` instead of `plan_code` and send them as such. `plan_code` is now optional; passing both, or `interval`/`currency` without a family, raises `NozleValidationError` before any request.

## 0.20.0 — 2026-10-28

### Added

- Typed margin results: `MarginSummary`, `MarginByCustomer`, `MarginByMetric`, `MarginByPlan`, `MarginByModel`, `MarginTrend` (and their row types), from the Engine schemas, returned by `margin.summary()`, `by_customer()`, `by_metric()`, `by_plan()`, `by_model()` and `trend()`.
- `NO_ACTIVE_SUBSCRIPTION`: `NozleAPIError.has_code("active_subscription_not_found")` is also true for Core's `subscription_not_found`, so a customer with no active subscription is one case.

### Changed

- `can()` metadata is typed as string, number or boolean values (the Engine accepts any JSON object).
- Already in place, now covered by tests: the error message carries the server text, and an HTTP-date `Retry-After` is read for `retry_after` and for the retry delay.

## 0.19.0 — 2026-10-27

### Added

- A suspended workspace (unpaid Nozle invoices) answers every call with HTTP 402 and `code` `workspace_suspended`. It raises `NozleAPIError` (`status_code` 402, `code`) from `can()`, `track()`, `usage.check()` and every other call, sync and async, and is never retried or turned into an allowed or denied result. Tests and a README note added.
- `checkout()` and `checkout_invoice()` accept `cancel_url=` (sent as `cancel_url`): where Stripe sends a customer who leaves the hosted page, validated like `return_url`. A return URL may contain `{CHECKOUT_SESSION_ID}`.

### Changed

- **Breaking:** Stripe checkout is hosted only (Core 11.4.0, Engine 2.12.0). A Stripe `checkout()`, `checkout_invoice()`, plan change or Entity checkout returns `{"type": "hosted", "url", "payment_url", "checkout_id", ...}`: redirect the customer to `url`. Only a paid upgrade has a status call (`checkout_status(plan_upgrade_intent_id, ...)`); a Stripe `checkout_id` is the session id, not a status id, so confirm a new subscription or invoice through webhooks or by reloading billing state. The `type: "stripe"` result, `StripeCheckoutResult` and every `client_secret` / `clientSecret` are removed, including on `entity_subscriptions.checkout_many()` (now `type` `hosted` with `url`). `HostedCheckoutResult` gains `url`.
- The billing portal example no longer reads `STRIPE_PUBLISHABLE_KEY`.

## 0.18.0 — 2026-10-27

### Added

- `customers.import_billing_state(customer_id, body, *, idempotency_key)` imports a subscription another system already bills (Core `POST /customers/{customer_id}/billing-state-imports`; the SDK wraps `body` in `billing_state_import`), and `customers.import_credit_balance(customer_id, body, *, idempotency_key)` records its opening credit balance (Engine `POST /customers/{customer_id}/credit-balance-imports`). Both need a secret key and an idempotency key of at most 255 bytes, return the unwrapped import and raise `NozleAPIError` on failure. Same on `AsyncNozle`.
- `margin.forecast(**params)` (`GET /margin/forecast`) and `margin.simulate(metric_code, new_price_dollars, **params)` (`POST /margin/simulate`), with typed results.
- `cost_intelligence.report(from_date=None, to_date=None, limit=None)`, `cost_intelligence.preview_shared_cost_allocation(pool_id, allocation_period_start)` and `cost_intelligence.allocate_shared_cost(pool_id, allocation_period_start)` (Engine `/cost-intelligence/*`, secret keys).

## 0.17.0 — 2026-10-26

### Added

- `can(customer_id, feature, metadata=None, *, subscription_id=None, entity_id=None)` evaluates one subscription (or one Entity's subscription) of a customer with several. Without a selector, a customer with several current subscriptions gets `allowed: False, reason: "subscription_required"`. Same on `AsyncNozle`.

### Changed

- `track()` with no `subscription_id` for a customer with no or several active subscriptions now raises `NozleAPIError` with `status_code` 422 (it was 200) and `code` `active_subscription_not_found` or `active_subscription_ambiguous`, the Engine's codes.

## 0.16.0 — 2026-10-24

### Added

- Plan versions. `Plan` has `version` and `is_current`; `plans()` returns current versions only.
- `subscribe(customer_id, plan_code, *, plan_version=None)` sells a chosen version of the plan code instead of the current one (needs Engine 2.7.0). The same customer and code again keep the subscription's version; another version of an existing subscription raises `NozleAPIError` 422 (`plan_version_change_not_supported`), an unknown version 404 (`plan_version_not_found`).
- `client.plan_versions` (secret keys): `list(code)` lists every version with its price and active subscriptions, `get(code, version)` reads one, and `preview_migration`, `create_migration`, `list_migrations`, `get_migration` and `cancel_migration` move the subscriptions of one version to another, each at the end of its billing period. Migration items carry `move_status` (`pending`, `moved`, `canceled`). A second migration of a code while one runs raises `NozleAPIError` 409 (`plan_version_migration_running`). Same on `AsyncNozle`.

## 0.15.0 — 2026-10-23

### Changed

- `cancel_subscription(customer_id, subscription_id)` without `policy` no longer sends `end_of_period`. It sends no timing, so the organization's cancellation timing applies (new organizations default to `end_of_period`; existing organizations keep their setting). Pass `policy="end_of_period"` to keep the previous behaviour. Same on `AsyncNozle`.

### Added

- `cancel_subscription` accepts keyword-only `credit_action` (`credit`, `refund`, `offset`, `none`), `refund_mode` (`prorated`, `full`) and `final_invoice_action` (`generate`, `skip`). Options left as `None` are not sent and follow the subscription, then the organization's cancellation settings. Needs Engine with nozle-dev/engine#144.

## 0.14.0 — 2026-10-22

### Added

- `manage_payment_method(customer_id, return_url=None, *, idempotency_key=None)` lets a Stripe customer add or replace their saved payment method. It returns `ManagePaymentMethodResult` (`url`, `expires_at`, `provider: "stripe"`): redirect the customer to `url`. Needs a secret key. Razorpay customers pay each invoice by payment link, so for them it raises `NozleAPIError` with HTTP 422 and `has_code("razorpay_pays_by_link")`. Other Engine errors (`invalid_return_url`, `no_linked_payment_provider`, `provider_not_supported`, `missing_payment_provider_customer`) also raise `NozleAPIError`.

### Fixed

- `track()` errors carry the transaction ID that was sent. When the request fails (an error response, a network error or a timeout), the raised `NozleAPIError` or `NozleTransportError` has `transaction_id` set to the generated (or passed) ID. Before, a generated ID was lost with the error, so a retry sent a new ID and Engine could record the event twice. Retry with `transaction_id=error.transaction_id`. `NozleError.transaction_id` is `None` for every other call. Same on `AsyncNozle`.
- `CanResult.limit` and `CanResult.remaining` are typed `Optional[int]`. Engine sends `null` for both when the feature has no usage limit (a boolean feature, or a metered feature without a cap), so `None` means unlimited. Type checkers now flag code that uses them as numbers without checking for `None`; the values returned at runtime are unchanged.

## 0.13.1 — 2026-10-19

### Fixed

- `wrap_openai` and `wrap_anthropic` no longer change the client passed in. They return a view of it whose `create` records usage for that wrapper's `customer_id`, as the Go SDK returns a copy; every other attribute is read from the original client, and `isinstance` checks still pass. Before, each wrap replaced `create` on the shared client, so after wrapping one client for several customers a single call recorded an event for every one of them. Calls on the original client now record nothing, and wrapping an already wrapped client wraps its original. Use the returned client: code that ignored the return value and kept calling the original client no longer records usage.

## 0.13.0 — 2026-10-17

### Added

- `AsyncNozle`: the `Nozle` client for asyncio apps (FastAPI and the like). Same options (`api_key` or `NOZLE_SECRET_KEY`, `host`, `base_url`, `events_url`, `timeout`, `max_retries`, `retry_delay`), namespaces and methods, awaited; `async with AsyncNozle() as nozle:` or `await nozle.aclose()`. Requests go through `httpx.AsyncClient` (`pip install 'nozle-sdk[httpx]'`) and retries wait with `asyncio.sleep`. Each call is written once and runs on both clients, so validation, errors, retries, idempotency keys, timestamps and left-out unset fields are the same. `webhooks.verify` is awaited on `AsyncNozle` (a `jwt` endpoint reads the public key). `capture_async_http_client` and `wrap_http_client` on an `httpx.AsyncClient` record usage on the event loop with `AsyncNozle`. `Nozle` is unchanged.
- `nozle.alerts.list / get / create / update / delete` manage subscription usage alerts (`subscription_id=`) and wallet balance alerts (`customer_id=`, `wallet_code=`). `create(alert_type, code, thresholds, *, name=None, feature_code=None, ...)`; `feature_code` is required for the `feature_*` types. Typed as `Alert` and `AlertPage`.
- `nozle.invoices.list(customer_id, *, status=None, payment_status=None, payment_overdue=None, issuing_date_from=None, issuing_date_to=None, page=None, per_page=None)` (newest first; `payment_overdue=True` keeps only unpaid invoices past their due date), `get(invoice_id)` and `download_url(invoice_id)` (the PDF URL, or `None` while it is generated). Typed as `Invoice` and `InvoicePage`.
- `current_usage(customer_id, subscription_id=None)` and `past_usage(customer_id, subscription_id=None, *, periods=None)` (newest first, a list) read Core usage; without a subscription ID the customer's one active subscription is used. Typed as `CustomerUsage`.
- `nozle.webhooks.verify(body, headers, *, hmac_key=None)` checks `X-Nozle-Signature` (`jwt`: Nozle's public key, read once with the secret key and cached; `hmac`: the signing secret) and returns a `NozleWebhookEvent`, or raises `WebhookVerificationError` with a `code`. No new dependency.
- `api_key` is optional. When it is not passed, `Nozle()` reads the `NOZLE_SECRET_KEY` environment variable and raises `ValueError` if that is not set either. An explicit `api_key` is used as before.
- `host` keyword argument (default `https://api.nozle.app`). It fills in `base_url` (`{host}/engine`) and `events_url` (`{host}/core`) when they are not given. `base_url` and `events_url` keep their meaning and win over `host`.
- Opt-in retries: `Nozle(max_retries=2, retry_delay=0.5)` (default `max_retries=0`, no retries). Network errors, timeouts, `429` and `5xx` are retried with backoff, or after the `Retry-After` seconds when sent, only for requests that are safe to repeat: reads, calls that carry an `Idempotency-Key` (such as `usage.track`), event, cost event and customer writes, and the read-only `usage.check` and transition preview. `subscribe()`, `checkout()` and other writes without a key are never retried. The `timeout` applies to each attempt.
- `NozleAPIError.request_id` (the `X-Request-Id` response header, which Core sends and the Engine does not yet) and `NozleAPIError.retry_after` (seconds, from `Retry-After`, as a number or an HTTP date). Both are `None` when the service did not send them. The message is unchanged.
- `entity_subscriptions.change_plan` accepts `billing_time`, which the API already reads.
- Usage capture reports once per client and event code when no Feature has that code: `on_error` (or a `NozleTrackingWarning`) gets an error with `code == "feature_not_found"`, because the usage is stored but not billed. Recording is not blocked.
- Usage capture for any LLM client built on httpx or httpx2. `nozle.wrap_http_client(client, customer_id=...)` makes the client your SDK takes as `http_client` (for example `openai.DefaultHttpxClient()` or `anthropic.DefaultHttpxClient()`) record the token usage of every successful LLM response; `nozle.capture_http_client(...)` and `capture_async_http_client(...)` build a new client the same way. Usage is recognised by the response shape: OpenAI-compatible `usage` (any OpenAI-compatible API, including the Responses API), Anthropic `usage` and Gemini `usageMetadata`, for JSON responses and streams. Chat completion streams are asked for usage (`stream_options.include_usage`; `include_stream_usage=False` turns it off). The customer comes from `customer_id` (a string or a function per request) or the `X-Nozle-Customer-Id` request header, which is not sent to the provider. `X-Nozle-Feature` sets the `feature` property and is also removed. Recording runs in a background thread (with `AsyncNozle`, a task on the event loop) and never changes or fails the provider response; failures go to `on_error` or a `NozleTrackingWarning`. Install with `pip install 'nozle-sdk[httpx]'`.
- `nozle.record_usage(customer_id, model=..., input_tokens=..., output_tokens=...)` records usage your code read itself, and raises if it could not be recorded.

### Changed

- Usage events recorded by `wrap_openai`, `wrap_anthropic` and the new capture use the provider's response id as the transaction id (`llm-<format>-<id>`) when there is one, so the same response is never recorded twice. Events also carry `provider_host` when known.

### Removed

- `PingResult.version` and `SubscriptionOptions.blocked_reason` are removed from the types. The API has never sent either field, so code reading them always got nothing.

### Fixed

- `usage.track()` reuses the timestamp it filled in when the same idempotency key is sent again on the same client, so a retry no longer fails with 409 `idempotency_key_reused`. The client remembers the last 1000 keys; a retry from another process or after a restart must pass `timestamp`.
- Requests leave out every optional field you did not set instead of sending JSON `null`, so the API applies its defaults. This is done once in the transport for every call; it fixes `entity_subscriptions.checkout`, `change_plan`, `checkout_many` and `cancel`, the subscription transition `target_plan_code`, and an Entity upsert without a name (leaving the name out means the same as `null`).

## 0.12.0 — 2026-10-14

### Added

- `customers.upsert()` accepts `billing_configuration` (`CustomerBillingConfiguration`: `payment_provider`, `payment_provider_code`, `provider_customer_id`, `sync_with_provider`, `provider_payment_methods`) and sends it to Core, so a customer can be linked to an existing Stripe customer in the same call. Upserts without it send the same body as before.

## 0.11.0 — 2026-10-13

### Changed

- `track()` and provider wrappers submit Feature Events to the Go Engine endpoint at `base_url/api/v1/events` (default `https://api.nozle.app/engine/api/v1/events`). Payloads, authentication and transaction IDs are unchanged.
- Custom event-ingestion deployments must now set `base_url`. `events_url` continues to configure Core billing resources and subscription discovery.

## 0.10.0 — 2026-10-13

### Breaking

- **Credit systems read `nozle_id`.** `credit_systems.list()` now requires `nozle_id` in each credit system and takes `id` from it. The `lago_id` fallback is gone, so a server that still sends only the old key makes the call raise `NozleAPIError` ("credit system response was incomplete"). Ship this release together with the Nozle Core release that sends `nozle_id`.

### Added

- `NozleAPIError.code` and `NozleAPIError.has_code()` expose the API error code, including the codes listed in `error_details` such as `razorpay_requires_inr`, `payment_provider_required` and `invalid_checkout_signature`.
- Checkout result types now declare fields the API returns: `plan_upgrade_intent_id`, `external_entity_id` and `external_subscription_id` on Razorpay results; `checkout_id`, `amount_cents`, `currency`, `expires_at`, `invoice_id` and `plan_upgrade_intent_id` on `hosted` results; `amount_cents`, `currency`, `expires_at`, `invoice_id` and `plan_upgrade_intent_id` on `processing` results; `plan_upgrade_intent_id` on Stripe results; and `payment_source`, `invoice_id`, `amount_cents`, `transition_direction` and `transition_policy` on `completed` and `scheduled` results.
- README section covering Stripe and Razorpay checkout side by side, including `processing` results for Stripe and Razorpay, Stripe-hosted credit top-up links, idempotency edge cases, and the REST customer provider assignment note.

### Fixed

- `track()` no longer keeps using a stale subscription. `subscribe()`, `checkout()`, `cancel_subscription()` and `apply_subscription_transition()` clear that customer's cached subscription, so the next `track()` looks it up again. Changes made outside this client instance still need an explicit `subscription_id`.

### Removed

- `pending_subscription_id` from `ScheduledCheckoutResult`. The API never returned it; the pending subscription ID is `subscription_id`. The `pending_subscription_id` argument of `withdraw_pending_subscription_change()` is unchanged.

### Changed

- **License:** the package is now MIT-licensed (it was `AGPL-3.0-or-later`). Versions up to 0.9.1 stay under AGPL-3.0-or-later for those who received them.

- `check_and_deduct()` takes `credits` as the required quantity and `feature` as optional: call `check_and_deduct(customer_id, credits=5)`. A missing `credits` raises `NozleValidationError`.

### Deprecated

- `feature` on `check_and_deduct()` is accepted but ignored and never sent, because the endpoint never used it, so `check_and_deduct(customer_id, "completion", 5)` keeps working.
- The Stripe `publishable_key`, `stripe_account` and `url` fields are not returned by plan and invoice checkout; use your own Stripe publishable key.

## 0.9.1 — 2026-10-11

### Fixed

- Convert RFC 3339 Feature Event timestamps to Unix seconds when sending events to Core, including timestamps produced by the OpenAI and Anthropic wrappers. Numeric timestamps remain unchanged, and malformed strings fail before the HTTP request.

## 0.9.0 — 2026-09-29

### Added

- Selected-subscription options, signed plan-change quotes, and withdrawal of pending changes for merchant billing portals.
- Checkout and transition support for selected subscriptions, quote IDs, cancellation-date guards, and scoped status and verification.

See the [0.9.0 release](https://github.com/nozle-dev/nozle-python/releases/tag/v0.9.0) for installation and verification details.
