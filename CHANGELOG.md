# Changelog

## Unreleased

### Added

- Usage capture reports once per client and event code when no Feature has that code: `on_error` (or a `NozleTrackingWarning`) gets an error with `code == "feature_not_found"`, because the usage is stored but not billed. Recording is not blocked.
- Usage capture for any LLM client built on httpx or httpx2. `nozle.wrap_http_client(client, customer_id=...)` makes the client your SDK takes as `http_client` (for example `openai.DefaultHttpxClient()` or `anthropic.DefaultHttpxClient()`) record the token usage of every successful LLM response; `nozle.capture_http_client(...)` and `capture_async_http_client(...)` build a new client the same way. Usage is recognised by the response shape: OpenAI-compatible `usage` (any OpenAI-compatible API, including the Responses API), Anthropic `usage` and Gemini `usageMetadata`, for JSON responses and streams. Chat completion streams are asked for usage (`stream_options.include_usage`; `include_stream_usage=False` turns it off). The customer comes from `customer_id` (a string or a function per request) or the `X-Nozle-Customer-Id` request header, which is not sent to the provider. Recording runs in a background thread and never changes or fails the provider response; failures go to `on_error` or a `NozleTrackingWarning`. Install with `pip install 'nozle-sdk[httpx]'`.
- `nozle.record_usage(customer_id, model=..., input_tokens=..., output_tokens=...)` records usage your code read itself, and raises if it could not be recorded.

### Changed

- Usage events recorded by `wrap_openai`, `wrap_anthropic` and the new capture use the provider's response id as the transaction id (`llm-<format>-<id>`) when there is one, so the same response is never recorded twice. Events also carry `provider_host` when known.

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
