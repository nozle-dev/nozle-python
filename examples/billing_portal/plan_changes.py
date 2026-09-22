"""Authenticated plan-management adapter shared by the example HTTP endpoints."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from nozle.errors import NozleAPIError


class PlanError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def fields(body: Any, allowed: set[str]) -> None:
    if not isinstance(body, dict) or body.keys() - allowed:
        raise PlanError(400, "Invalid plan change fields.")


def value(item: Any, name: str, maximum: int = 255) -> str:
    if not isinstance(item, str) or not item.strip() or len(item.encode()) > maximum:
        raise PlanError(400, f"Invalid {name}.")
    return item


def plan(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": item["code"],
        "name": item["name"],
        "amountCents": item["amount_cents"],
        "currency": item["currency"],
        "interval": item["interval"],
    }


def checkout_status(status: str) -> str:
    return {
        "pending": "awaiting_payment",
        "applied": "succeeded",
        "payment_failed": "failed",
        "canceled": "failed",
    }.get(status, status)


class PlanService:
    def __init__(
        self, sdk: Any, store: Any, return_origin: str, stripe_publishable_key: Optional[str] = None
    ) -> None:
        self.sdk = sdk
        self.store = store
        self.return_origin = return_origin
        if stripe_publishable_key and not stripe_publishable_key.startswith(
            ("pk_test_", "pk_live_")
        ):
            raise ValueError("STRIPE_PUBLISHABLE_KEY must be a Stripe publishable key")
        self.stripe_publishable_key = stripe_publishable_key

    def payment(self, result: Any) -> Any:
        if (
            result
            and result.get("type") == "stripe"
            and not result.get("url")
            and not result.get("publishable_key")
            and self.stripe_publishable_key
        ):
            return {**result, "publishable_key": self.stripe_publishable_key}
        return result

    def load(self, customer_id: str, subscription_id: str) -> dict[str, Any]:
        options = self.sdk.subscription_options(customer_id, subscription_id)
        checkout = None
        summary = options.get("checkout")
        status = checkout_status(summary["status"]) if summary else "none"
        if summary:
            current = self.sdk.checkout_status(
                summary["id"], customer_id=customer_id, subscription_id=subscription_id
            )
            status = checkout_status(current["status"])
            if status in ("awaiting_payment", "processing"):
                checkout = self.payment(current.get("checkout"))
        if status not in (
            "none",
            "awaiting_payment",
            "processing",
            "succeeded",
            "failed",
            "expired",
            "needs_review",
        ):
            status = "needs_review"
        subscription = options["subscription"]
        pending = options["pending_change"]
        reason = options.get("blocked_reason")
        if not reason and subscription["ending_at"]:
            reason = "Resolve your scheduled cancellation before changing plans."
        if not reason and pending:
            reason = "Withdraw your pending plan change before choosing another plan."
        if not reason and status in ("awaiting_payment", "processing", "needs_review"):
            reason = "Resolve your existing checkout before changing plans."
        return {
            "subscriptionId": subscription["external_id"],
            "status": subscription["status"],
            "currentPlan": plan(subscription["plan"]),
            "endingAt": subscription["ending_at"],
            "pendingChange": {
                "id": pending["id"],
                "plan": plan(pending["plan"]),
                "effectiveAt": pending["effective_at"],
            }
            if pending
            else None,
            "eligiblePlans": [
                {**plan(item), "operation": item["direction"]} for item in options["eligible_plans"]
            ],
            "blockedReason": reason,
            "checkoutStatus": status,
            "checkout": checkout,
        }

    def once(
        self,
        customer_id: str,
        body: dict[str, Any],
        operation: str,
        prepare: Callable[[], Any],
        apply: Callable[[str, Any], Any],
    ) -> Any:
        client_key = value(body.get("idempotencyKey"), "idempotency key")
        key = hashlib.sha256(
            json.dumps(
                [customer_id, client_key], separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        fingerprint = json.dumps(
            [
                operation,
                body.get("subscriptionId"),
                body.get("targetPlanCode"),
                body.get("quoteToken"),
                body.get("pendingChangeId"),
                body.get("returnUrl"),
            ],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self.store.lock:
            entry = self.store.entries.get(key)
            if entry and entry["fingerprint"] != fingerprint:
                raise PlanError(409, "Idempotency key already used.")
            if entry and entry["complete"]:
                return entry["result"]
            if not entry:
                entry = {"fingerprint": fingerprint, "complete": False, "context": prepare()}
                self.store.save(key, entry)
            result = apply(key, entry["context"])
            self.store.save(key, {**entry, "complete": True, "result": result})
            return result

    def dispatch(self, customer_id: str, path: str, body: Any) -> Any:
        try:
            return self._dispatch(customer_id, path, body)
        except NozleAPIError as error:
            if error.status_code == 409:
                raise PlanError(
                    409, "Your plan or quote changed. Refresh and confirm again."
                ) from None
            if error.status_code == 404:
                raise PlanError(404, "Subscription or checkout not found.") from None
            raise

    def _dispatch(self, customer_id: str, path: str, body: Any) -> Any:
        action = path[len("/api/billing/plans/") :]
        if action in ("load", "status"):
            fields(body, {"subscriptionId"})
            return self.load(customer_id, value(body.get("subscriptionId"), "subscription ID"))
        if action == "preview":
            fields(body, {"subscriptionId", "targetPlanCode"})
            current = self.sdk.preview_subscription_change(
                customer_id,
                value(body.get("subscriptionId"), "subscription ID"),
                value(body.get("targetPlanCode"), "target plan"),
            )
            return {
                "operation": current["transition_direction"],
                "timing": current["timing"],
                "currency": current["currency"],
                "creditAmountCents": current["credit_amount_cents"],
                "debitAmountCents": current["debit_amount_cents"],
                "netAmountCents": current["net_amount_cents"],
                "amountDueNowCents": current["amount_due_now_cents"],
                "amountDueAtEffectiveCents": current["amount_due_at_effective_cents"],
                "effectiveAt": current["effective_at"],
                "renewalAt": current["renewal_at"],
                "quoteToken": current["quote_id"],
            }
        if action == "apply":
            fields(
                body,
                {"subscriptionId", "targetPlanCode", "quoteToken", "idempotencyKey", "returnUrl"},
            )
            subscription_id = value(body.get("subscriptionId"), "subscription ID")
            target = value(body.get("targetPlanCode"), "target plan")
            quote_id = value(body.get("quoteToken"), "quote", 16_384)
            return_url = value(body.get("returnUrl"), "return URL", 2048)
            try:
                parsed = urlsplit(return_url)
                origin = f"{parsed.scheme}://{parsed.netloc}"
                if origin != self.return_origin or parsed.username or parsed.password:
                    raise ValueError
            except ValueError:
                raise PlanError(400, "Return URL must use the merchant origin.") from None

            def prepare() -> dict[str, str]:
                state = self.load(customer_id, subscription_id)
                selected = next(
                    (item for item in state["eligiblePlans"] if item["code"] == target), None
                )
                if state["blockedReason"] or not selected:
                    raise PlanError(
                        409, "Plan change is no longer available. Refresh your subscription."
                    )
                return {"operation": selected["operation"]}

            def apply(key: str, context: Any) -> Any:
                if context["operation"] == "downgrade":
                    result = self.sdk.apply_subscription_transition(
                        {
                            "customer_id": customer_id,
                            "subscription_id": subscription_id,
                            "operation": "downgrade",
                            "target_plan_code": target,
                            "timing": "end_of_period",
                            "billing_anchor": "keep_anchor",
                            "quote_id": quote_id,
                        },
                        idempotency_key=key,
                    )
                    changed = result["subscription_transition"]
                    return {
                        "type": "scheduled",
                        "status": changed.get("status"),
                        "subscription_id": changed.get("subscription_id"),
                        "pending_subscription_id": changed.get("subscription_id"),
                        "external_subscription_id": subscription_id,
                        "plan_code": changed.get("plan_code"),
                        "effective_at": changed["effective_at"],
                        "currency": changed.get("currency"),
                    }
                return self.payment(
                    self.sdk.checkout(
                        customer_id,
                        target,
                        return_url,
                        subscription_id=subscription_id,
                        quote_id=quote_id,
                        idempotency_key=key,
                    )
                )

            return self.once(customer_id, body, "change", prepare, apply)
        if action == "withdraw":
            fields(body, {"subscriptionId", "pendingChangeId", "idempotencyKey"})
            subscription_id = value(body.get("subscriptionId"), "subscription ID")
            pending_id = value(body.get("pendingChangeId"), "pending change ID")

            def withdraw(key: str, _: Any) -> dict[str, Any]:
                self.sdk.withdraw_pending_subscription_change(
                    customer_id, subscription_id, pending_id, idempotency_key=key
                )
                return {}

            return self.once(customer_id, body, "withdraw", lambda: None, withdraw)
        if action in ("checkout-status", "checkout-verify"):
            fields(
                body,
                {"subscriptionId", "checkoutId"}
                if action == "checkout-status"
                else {"subscriptionId", "checkoutId", "verification"},
            )
            subscription_id = value(body.get("subscriptionId"), "subscription ID")
            checkout_id = value(body.get("checkoutId"), "checkout ID")
            if action == "checkout-status":
                return self.sdk.checkout_status(
                    checkout_id, customer_id=customer_id, subscription_id=subscription_id
                )
            fields(
                body.get("verification"),
                {"razorpay_order_id", "razorpay_payment_id", "razorpay_signature"},
            )
            for name in ("razorpay_order_id", "razorpay_payment_id", "razorpay_signature"):
                value(body["verification"].get(name), name)
            return self.sdk.verify_checkout(
                checkout_id,
                body["verification"],
                customer_id=customer_id,
                subscription_id=subscription_id,
            )
        raise PlanError(404, "Not found.")
