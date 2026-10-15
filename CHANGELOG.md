# Changelog

## Unreleased

### Added

- `api_key` is optional. When it is not passed, `Nozle()` reads the `NOZLE_SECRET_KEY` environment variable and raises `ValueError` if that is not set either. An explicit `api_key` is used as before.
- `host` keyword argument (default `https://api.nozle.app`). It fills in `base_url` (`{host}/engine`) and `events_url` (`{host}/core`) when they are not given. `base_url` and `events_url` keep their meaning and win over `host`.
- Opt-in retries: `Nozle(max_retries=2, retry_delay=0.5)` (default `max_retries=0`, no retries). Network errors, timeouts, `429` and `5xx` are retried with backoff, or after the `Retry-After` seconds when sent, only for requests that are safe to repeat: reads, calls that carry an `Idempotency-Key` (such as `usage.track`), event, cost event and customer writes, and the read-only `usage.check` and transition preview. `subscribe()`, `checkout()` and other writes without a key are never retried. The `timeout` applies to each attempt.
- `NozleAPIError.request_id` (the `X-Request-Id` response header, which Core sends and the Engine does not yet) and `NozleAPIError.retry_after` (seconds, from `Retry-After`, as a number or an HTTP date). Both are `None` when the service did not send them. The message is unchanged.
- A contract test: `tests/contract` checks that each SDK response type declares only keys the API schema has and requires only keys the schema requires. `scripts/sync_openapi_contract.py` refreshes the schemas from `nozle-openapi.yaml`.
- `entity_subscriptions.change_plan` accepts `billing_time`, which the API already reads.

### Removed

- `PingResult.version` and `SubscriptionOptions.blocked_reason` are removed from the types. The API has never sent either field, so code reading them always got nothing.

### Fixed

- `usage.track()` reuses the timestamp it filled in when the same idempotency key is sent again on the same client, so a retry no longer fails with 409 `idempotency_key_reused`. The client remembers the last 1000 keys; a retry from another process or after a restart must pass `timestamp`.
- Requests leave out every optional field you did not set instead of sending JSON `null`, so the API applies its defaults. This is done once in the transport for every call; it fixes `entity_subscriptions.checkout`, `change_plan`, `checkout_many` and `cancel`, the subscription transition `target_plan_code`, and an Entity upsert without a name (leaving the name out means the same as `null`).
- A request contract test (`tests/contract/test_request_contract.py`) checks the body of every SDK call against the API request schema: no unknown key, no `null` where it is not allowed, and enum values from the schema. `scripts/sync_openapi_requests.py` refreshes the schemas from `nozle-openapi.yaml`.

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
