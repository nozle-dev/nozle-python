# React subscription management through the Python SDK

This runnable merchant server connects `BillingPortal` to the existing subscription transitions and payment-backed checkout. Customers can cancel at period end, keep their subscription, upgrade, schedule a downgrade, and withdraw the exact pending change. The merchant authenticates the customer; Nozle Engine checks that the selected external subscription belongs to that customer and organization. No secret API key goes to React.

## Run

Use Python 3.9 or newer. From this repository root:

```sh
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q tests/test_billing_portal_example.py tests/test_cancellation_guard.py tests/test_plan_change_example.py tests/test_subscription_management.py
```

Export the variables listed in `.env.example` using your shell or secret manager, then run:

```sh
cd examples/billing_portal
../../.venv/bin/python server.py
```

`NOZLE_ENGINE_URL` and `NOZLE_CORE_URL` are explicit: local/VM test APIs work without contacting production. Use `NOZLE_CORE_API_KEY` if Core requires a different credential. Set `NOZLE_PORTAL_API_URL` to the Core URL reachable by the browser, which may differ from the server's internal URL. React permits HTTPS and loopback HTTP.

Choose a dedicated customer in `DEMO_CUSTOMER_ID` and a random `DEMO_LOGIN_TOKEN` of at least 32 characters. Open `http://localhost:4243` to sign in. This token is only the demo's merchant login credential; it is not a Nozle key. Bind to loopback by default. Set `HOST` only when running behind your development proxy or inside an isolated test container.

The React controls and confirmation-date guard must come from the associated feature builds. Existing SDK installations without `expected_effective_at`, or Engine/Core installations without `expected_effective_at`, cannot provide the atomic date check. Deploy the compatible backend before enabling the action adapter.

## React adapter

The JavaScript SDK repository includes a runnable React app at `examples/billing-portal/web`. Checkout return URLs require HTTPS. Start this Python server with `MERCHANT_ORIGIN=https://127.0.0.1:5179`. Build the React package in that repository, then run `npm install` in the web example. Export absolute `DEV_TLS_CERT` and `DEV_TLS_KEY` paths for a local development certificate (for example, generated outside the repository with `mkcert 127.0.0.1`), then run `MERCHANT_PORT=4243 npm run dev`. Open `https://127.0.0.1:5179` and sign in with the demo login token. Vite proxies `/api` to this Python integration over loopback HTTP. Cancellation-only testing can use loopback HTTP; checkout needs HTTPS.

Serve React and these endpoints from the same origin, or proxy `/api` from Vite to port 4243. Set `MERCHANT_ORIGIN` to that exact browser origin. Sign in through `POST /api/login` with `{ "token": "your demo login token" }`; subsequent calls use the HttpOnly cookie. Requests require JSON and an exact `Origin` match.

```tsx
import { BillingPortal, BillingPortalError, type CancellationActions, type BillingPortalSession } from '@nozle-js/react';

async function post<T>(path: string, body: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    method: 'POST', credentials: 'same-origin', signal,
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new BillingPortalError(
      response.status === 401 || response.status === 403 ? 'unauthorized' :
      response.status === 409 ? 'changed' : 'request',
    );
  }
  return response.json();
}

// Keep these callbacks stable across renders; customer changes should replace them.
const createSession = () => post<BillingPortalSession>('/api/billing/session', {});
const cancellationActions: CancellationActions = {
  preview: ({ signal, ...body }) => post('/api/billing/cancellation/preview', body, signal),
  apply: ({ signal, ...body }) => post('/api/billing/cancellation', body, signal),
};

export function CustomerBilling() {
  return <BillingPortal createSession={createSession} cancellationActions={cancellationActions} />;
}
```

After any apply result or timeout, the React control reads persisted customer-scoped GraphQL state before reporting success or retrying. A preview carries the backend's exact effective-date string, including microseconds. The server re-previews on the first attempt and also sends `expected_effective_at` for Core's atomic comparison; HTTP 409 requires a new confirmation.

## Handler boundaries

- Cancellation action bodies contain only an external `subscriptionId`, `operation` (`cancel` or `uncancel`), and, on apply, `idempotencyKey` plus `expectedEffectiveAt` for cancellation. Customer IDs, refund settings, plan changes, and immediate timing are rejected.
- Cancellation always explicitly uses `end_of_period`. Keep sends `uncancel` without settlement overrides. It restores renewal while the subscription is active; it does not restart an ended subscription or restore a removed pending downgrade.
- Core owns durable idempotency. The local action file binds the authenticated customer and confirmation to the same upstream key, remembers completed calls, and allows retries after a lost response to bypass a now-invalid fresh preview. Keep the same key for a retry. Create a new key only after a new confirmation.
- `ACTION_STORE_PATH` defaults to `.billing-portal/actions.json` under the current directory, with private file permissions. Run from the example directory, whose `.gitignore` excludes the store. Use a transactional shared database and your app's existing authentication/session store for multiple workers. This file store and token login are for one development process, not a replacement for merchant production auth. Keep replay records for your supported retry window.
- No raw SDK error or token is logged or returned as an error. Portal session tokens are intentionally returned only to the authenticated customer; keep response caching disabled. Preserve your application's TLS, CSRF, session expiry, rate limits, and customer-to-subscription authorization.

## Verify a dedicated backend fixture

### Access enforcement

The portal changes billing state; your application must enforce entitlement checks on its server. Core currently runs subscription termination hourly at minute 05, and the Engine refreshes its entitlement cache each minute. Exact removal of access at the scheduled cancellation time therefore remains an M1 launch gate. Tests that advance the clock and invoke the Core job verify lifecycle behavior, but do not prove the timing of a deployed scheduler or cache refresh.

Create a renewing test customer and subscription whose external IDs both start `sdk-cancel-test-`. Export `DEMO_SUBSCRIPTION_ID`, `DEMO_CUSTOMER_ID`, and the login variables used by the server, then:

```sh
MERCHANT_URL=http://localhost:4243 ../../.venv/bin/python verify.py
```

The verifier authenticates, checks tampering and stale-date rejection, cancels and replays, verifies persisted period-end cancellation, keeps and replays, and verifies the original active plan is renewing. It restores only its explicitly named test fixture. It refuses the protected billing-lab customer. The same driver can target the Node merchant server by changing `MERCHANT_URL` and `MERCHANT_ORIGIN`.

For package verification, build with `python -m build`, install the wheel into an empty virtual environment, copy `examples/billing_portal` and the example tests outside the repository, and run them with that environment. The tests include the actual SDK wire request and nested response contract in addition to authentication, conflict, and retry cases. Live lifecycle/renewal evidence comes from the isolated backend tests, not these mocked transport checks.

## Upgrade, downgrade and checkout recovery

The same authenticated server now implements `planChangeActions`. Load and status return the selected subscription's current plan, pending plan, eligible policy-defined targets, and persisted checkout state. Preview returns backend amounts as exact minor-unit values, currency, dates, and an opaque signed quote. Apply passes that quote and an idempotency key to payment-backed checkout for upgrades, or the existing end-of-period downgrade transition. Withdrawal targets the precise pending subscription UUID and does not cancel the active plan.

```tsx
import type { PlanChangeActions } from '@nozle-js/react';
const planChangeActions: PlanChangeActions = {
  load: ({signal, ...body}) => post('/api/billing/plans/load', body, signal),
  status: ({signal, ...body}) => post('/api/billing/plans/status', body, signal),
  preview: ({signal, ...body}) => post('/api/billing/plans/preview', body, signal),
  apply: ({signal, ...body}) => post('/api/billing/plans/apply', body, signal),
  withdraw: ({signal, ...body}) => post('/api/billing/plans/withdraw', body, signal),
  verifyCheckout: ({signal, ...body}) => post('/api/billing/plans/checkout-verify', body, signal),
  getCheckoutStatus: ({signal, ...body}) => post('/api/billing/plans/checkout-status', body, signal),
};
// Add planChangeActions={planChangeActions} beside cancellationActions on BillingPortal.
```

The corresponding Python methods are `subscription_options(customer_id, subscription_id)`, `preview_subscription_change(customer_id, subscription_id, plan_code)`, `checkout(customer_id, plan_code, return_url, subscription_id=…, quote_id=…, idempotency_key=…)`, and `withdraw_pending_subscription_change(customer_id, subscription_id, pending_subscription_id, idempotency_key=…)`. `checkout_status` and `verify_checkout` accept optional `customer_id` and `subscription_id` keyword scope; the merchant always supplies it for customer checkout recovery and verification. Existing calls remain compatible.

A return URL must use `MERCHANT_ORIGIN`; browsers cannot select another customer or override immediate/refund/settlement behavior. The server restricts new changes while cancellation, a pending plan, or unresolved payment is present. A payment form opening or a provider callback is not proof of activation: React reloads the authoritative state and displays processing/failure until the selected plan actually changes. The private replay store now also holds scoped checkout results; keep its private permissions and replace it with your application's transactional store for production workers.

For embedded Stripe, configure `STRIPE_PUBLISHABLE_KEY` with the matching Stripe `pk_test_…` or `pk_live_…` public key when Core does not include one. Hosted checkout URLs remain preferred when returned. Never put a Stripe secret key in this variable. Razorpay uses its scoped checkout's public key plus authenticated verification/status callbacks. Live provider payment verification is separate from local transport tests and zero-due demos.

To run the real service zero-due upgrade/downgrade/withdrawal scenario, configure a second dedicated subscription in `DEMO_OTHER_SUBSCRIPTION_ID` and a policy-enabled zero-due upgrade target in `DEMO_UPGRADE_PLAN_CODE`, alongside the existing fixture/login variables. Run the JavaScript SDK repository’s `examples/billing-portal/verify-plan-changes.mjs` using Node with `MERCHANT_URL` set to this Python server; it refuses non-test fixture IDs and any upgrade amount due now above zero, proves the other subscription stays unchanged, and leaves the selected fixture active on the upgrade plan with no pending change. Provider-paid flows should be exercised through the included React demo with sandbox credentials.
