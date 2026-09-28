from pathlib import Path
from typing import Any

import pytest

from examples.billing_portal.plan_changes import PlanError
from examples.billing_portal.server import ActionStore, BillingService
from nozle.errors import NozleAPIError

LOWER = {
    "code": "basic",
    "name": "Basic",
    "amount_cents": "500",
    "currency": "JPY",
    "interval": "monthly",
}
PRO = {**LOWER, "code": "pro", "name": "Pro", "amount_cents": "1500"}
APPLY = {
    "subscriptionId": "external",
    "targetPlanCode": "pro",
    "quoteToken": "opaque.quote",
    "idempotencyKey": "confirm",
    "returnUrl": "https://merchant.example/billing",
}


class PlanSDK:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.fail_once = False
        self.conflict = False
        self.options: dict[str, Any] = {
            "subscription": {
                "id": "internal",
                "external_id": "external",
                "plan_code": "basic",
                "status": "active",
                "ending_at": None,
                "plan": LOWER,
            },
            "pending_change": None,
            "eligible_plans": [{**PRO, "direction": "upgrade", "timing": "immediate"}],
            "checkout": None,
        }

    def subscription_options(self, *args: Any) -> Any:
        self.calls.append(("options", *args))
        return self.options

    def preview_subscription_change(self, *args: Any) -> Any:
        self.calls.append(("preview", *args))
        return {
            "transition_direction": "upgrade",
            "timing": "immediate",
            "currency": "JPY",
            "credit_amount_cents": "0",
            "debit_amount_cents": "900719925474099300",
            "net_amount_cents": "900719925474099300",
            "amount_due_now_cents": "900719925474099300",
            "amount_due_at_effective_cents": "0",
            "effective_at": "2030-01-01T00:00:00.123456Z",
            "renewal_at": None,
            "quote_id": "opaque.quote",
        }

    def checkout(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("checkout", *args, kwargs))
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("timeout")
        if self.conflict:
            raise NozleAPIError("checkout", 409, {"private": "do not echo"})
        return {
            "type": "stripe",
            "client_secret": "customer-payment-secret",
            "publishable_key": "pk_public",
            "checkout_id": "intent",
        }

    def apply_subscription_transition(self, params: Any, **kwargs: Any) -> Any:
        self.calls.append(("transition", params, kwargs))
        return {
            "subscription_transition": {
                "status": "pending",
                "plan_code": "basic",
                "subscription_id": "pending-internal",
                "effective_at": "2030-01-01T00:00:00.123456Z",
                "currency": "JPY",
            }
        }

    def withdraw_pending_subscription_change(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("withdraw", *args, kwargs))
        return {}

    def checkout_status(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("status", *args, kwargs))
        return {
            "status": "awaiting_payment",
            "checkout": {"type": "stripe", "client_secret": "customer-payment-secret"},
        }

    def verify_checkout(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(("verify", *args, kwargs))
        return {"status": "processing"}


def merchant(path: Path, sdk: PlanSDK) -> BillingService:
    return BillingService(
        sdk,
        ActionStore(path / "actions.json"),
        lambda _: {},
        return_origin="https://merchant.example",
    )


def route(action: str) -> str:
    return "/api/billing/plans/" + action


def test_authoritative_policy_and_exact_quote_values(tmp_path: Path) -> None:
    sdk = PlanSDK()
    sdk.options["eligible_plans"][0]["amount_cents"] = "100"
    server = merchant(tmp_path, sdk)
    state = server.dispatch("authenticated", route("load"), {"subscriptionId": "external"})
    assert state["eligiblePlans"][0]["operation"] == "upgrade"
    assert state["eligiblePlans"][0]["amountCents"] == "100"
    preview = server.dispatch(
        "authenticated", route("preview"), {"subscriptionId": "external", "targetPlanCode": "pro"}
    )
    assert preview["amountDueNowCents"] == "900719925474099300"
    assert preview["effectiveAt"] == "2030-01-01T00:00:00.123456Z"
    assert preview["quoteToken"] == "opaque.quote"


def test_upgrade_uses_payment_checkout_and_replays_after_restart(tmp_path: Path) -> None:
    sdk = PlanSDK()
    result = merchant(tmp_path, sdk).dispatch("authenticated", route("apply"), APPLY)
    assert merchant(tmp_path, sdk).dispatch("authenticated", route("apply"), APPLY) == result
    calls = [call for call in sdk.calls if call[0] == "checkout"]
    assert len(calls) == 1
    assert calls[0][1:4] == ("authenticated", "pro", "https://merchant.example/billing")
    assert calls[0][4]["subscription_id"] == "external"
    assert calls[0][4]["quote_id"] == "opaque.quote"
    assert sdk.options["subscription"]["plan_code"] == "basic"


def test_lost_response_retries_same_key_without_rejecting_new_pending_checkout(
    tmp_path: Path,
) -> None:
    sdk = PlanSDK()
    sdk.fail_once = True
    with pytest.raises(TimeoutError):
        merchant(tmp_path, sdk).dispatch("authenticated", route("apply"), APPLY)
    sdk.options["checkout"] = {"id": "intent", "status": "pending"}
    sdk.options["eligible_plans"] = []
    merchant(tmp_path, sdk).dispatch("authenticated", route("apply"), APPLY)
    calls = [call for call in sdk.calls if call[0] == "checkout"]
    assert calls[0][4]["idempotency_key"] == calls[1][4]["idempotency_key"]
    assert len([call for call in sdk.calls if call[0] == "options"]) == 1


def test_downgrade_uses_quoted_period_end_transition_and_exact_pending_withdrawal(
    tmp_path: Path,
) -> None:
    sdk = PlanSDK()
    sdk.options["subscription"].update({"plan": PRO, "plan_code": "pro"})
    sdk.options["eligible_plans"] = [{**LOWER, "direction": "downgrade", "timing": "end_of_period"}]
    server = merchant(tmp_path, sdk)
    result = server.dispatch("authenticated", route("apply"), {**APPLY, "targetPlanCode": "basic"})
    assert result["type"] == "scheduled"
    assert result["effective_at"] == "2030-01-01T00:00:00.123456Z"
    transition = next(call[1] for call in sdk.calls if call[0] == "transition")
    assert transition == {
        "customer_id": "authenticated",
        "subscription_id": "external",
        "operation": "downgrade",
        "target_plan_code": "basic",
        "timing": "end_of_period",
        "billing_anchor": "keep_anchor",
        "quote_id": "opaque.quote",
    }
    request = {
        "subscriptionId": "external",
        "pendingChangeId": "pending-internal",
        "idempotencyKey": "withdraw",
    }
    server.dispatch("authenticated", route("withdraw"), request)
    merchant(tmp_path, sdk).dispatch("authenticated", route("withdraw"), request)
    withdrawals = [call for call in sdk.calls if call[0] == "withdraw"]
    assert len(withdrawals) == 1
    assert withdrawals[0][1:4] == ("authenticated", "external", "pending-internal")


@pytest.mark.parametrize(
    "change",
    [
        {"customerId": "victim"},
        {"timing": "immediate"},
        {"returnUrl": "https://attacker.example/"},
        {"quoteToken": ""},
    ],
)
def test_rejects_identity_policy_and_redirect_tampering(tmp_path: Path, change: Any) -> None:
    sdk = PlanSDK()
    with pytest.raises(PlanError) as error:
        merchant(tmp_path, sdk).dispatch("authenticated", route("apply"), {**APPLY, **change})
    assert error.value.status == 400
    assert not sdk.calls


def test_pending_cancellation_blocks_new_plan_change(tmp_path: Path) -> None:
    sdk = PlanSDK()
    sdk.options["subscription"]["ending_at"] = "2030-01-01T00:00:00Z"
    with pytest.raises(PlanError) as error:
        merchant(tmp_path, sdk).dispatch("authenticated", route("apply"), APPLY)
    assert error.value.status == 409
    assert not any(call[0] == "checkout" for call in sdk.calls)


def test_checkout_recovery_and_verification_are_customer_and_subscription_scoped(
    tmp_path: Path,
) -> None:
    sdk = PlanSDK()
    sdk.options["checkout"] = {"id": "intent", "status": "pending", "plan_code": "pro"}
    server = merchant(tmp_path, sdk)
    state = server.dispatch("authenticated", route("status"), {"subscriptionId": "external"})
    assert state["checkoutStatus"] == "awaiting_payment"
    assert state["checkout"]["type"] == "stripe"
    verification = {
        "razorpay_order_id": "order",
        "razorpay_payment_id": "payment",
        "razorpay_signature": "signature",
    }
    server.dispatch(
        "authenticated",
        route("checkout-verify"),
        {"subscriptionId": "external", "checkoutId": "intent", "verification": verification},
    )
    scope = {"customer_id": "authenticated", "subscription_id": "external"}
    assert next(call for call in sdk.calls if call[0] == "status")[1:] == ("intent", scope)
    assert next(call for call in sdk.calls if call[0] == "verify")[1:] == (
        "intent",
        verification,
        scope,
    )


def test_atomic_conflict_is_safe_409(tmp_path: Path) -> None:
    sdk = PlanSDK()
    sdk.conflict = True
    with pytest.raises(PlanError) as error:
        merchant(tmp_path, sdk).dispatch("authenticated", route("apply"), APPLY)
    assert error.value.status == 409
    assert "private" not in str(error.value)


def test_embedded_stripe_requires_only_public_configuration(tmp_path: Path) -> None:
    sdk = PlanSDK()
    sdk.checkout = lambda *args, **kwargs: {"type": "stripe", "client_secret": "customer-secret"}  # type: ignore[method-assign]
    server = BillingService(
        sdk,
        ActionStore(tmp_path / "actions.json"),
        lambda _: {},
        return_origin="https://merchant.example",
        stripe_publishable_key="pk_test_public",
    )
    assert (
        server.dispatch("authenticated", route("apply"), APPLY)["publishable_key"]
        == "pk_test_public"
    )
    with pytest.raises(ValueError, match="publishable key"):
        BillingService(
            sdk,
            ActionStore(tmp_path / "bad.json"),
            lambda _: {},
            stripe_publishable_key="sk_secret",
        )
