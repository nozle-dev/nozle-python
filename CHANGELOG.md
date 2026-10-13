# Changelog

## 0.10.0 — 2026-10-13

### Breaking

- **Credit systems read `nozle_id`.** `credit_systems.list()` now requires `nozle_id` in each credit system and takes `id` from it. The `lago_id` fallback is gone, so a server that still sends only the old key makes the call raise `NozleAPIError` ("credit system response was incomplete"). Ship this release together with the Nozle Core release that sends `nozle_id`.

### Added

- `NozleAPIError.code` and `NozleAPIError.has_code()` expose the API error code, including the codes listed in `error_details` such as `razorpay_requires_inr`, `payment_provider_required` and `invalid_checkout_signature`.
- Checkout result types now declare fields the API returns: `plan_upgrade_intent_id`, `external_entity_id` and `external_subscription_id` on Razorpay results; `checkout_id`, `amount_cents`, `currency`, `expires_at`, `invoice_id` and `plan_upgrade_intent_id` on `hosted` results; `amount_cents`, `currency`, `expires_at`, `invoice_id` and `plan_upgrade_intent_id` on `processing` results; `plan_upgrade_intent_id` on Stripe results; and `payment_source`, `invoice_id`, `amount_cents`, `transition_direction` and `transition_policy` on `completed` and `scheduled` results.
- README section covering Stripe and Razorpay checkout side by side, including `processing` results for Stripe and Razorpay, Stripe-hosted credit top-up links, idempotency edge cases, and the REST customer provider assignment note.

### Changed

- `check_and_deduct()` takes `credits` as the required quantity and `feature` as optional: call `check_and_deduct(customer_id, credits=5)`. A missing `credits` raises `NozleValidationError`.

### Deprecated

- `feature` on `check_and_deduct()` is accepted but ignored and never sent, because the endpoint never used it, so `check_and_deduct(customer_id, "completion", 5)` keeps working.
- `pending_subscription_id` on `scheduled` results is never returned. Use `subscription_id`. The Stripe `publishable_key`, `stripe_account` and `url` fields are not returned by plan and invoice checkout either; use your own Stripe publishable key.

## 0.9.1 — 2026-10-11

### Fixed

- Convert RFC 3339 Feature Event timestamps to Unix seconds when sending events to Core, including timestamps produced by the OpenAI and Anthropic wrappers. Numeric timestamps remain unchanged, and malformed strings fail before the HTTP request.

## 0.9.0 — 2026-09-29

### Added

- Selected-subscription options, signed plan-change quotes, and withdrawal of pending changes for merchant billing portals.
- Checkout and transition support for selected subscriptions, quote IDs, cancellation-date guards, and scoped status and verification.

See the [0.9.0 release](https://github.com/nozle-dev/nozle-python/releases/tag/v0.9.0) for installation and verification details.
