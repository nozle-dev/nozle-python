from __future__ import annotations

from typing import Sequence, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import (
    path_segment,
    require_non_empty,
    require_secret_key,
    validate_entity_id,
    validate_idempotency_key,
)
from nozle.errors import NozleValidationError
from nozle.types import (
    CheckoutResult,
    EntitySubscription,
    EntitySubscriptionCancelResult,
    EntitySubscriptionCheckoutItem,
    EntitySubscriptionCheckoutManyResult,
    EntitySubscriptionList,
    SubscriptionTransitionCreditAction,
    SubscriptionTransitionFinalInvoiceAction,
    SubscriptionTransitionRefundMode,
    SubscriptionTransitionTiming,
)


class EntitySubscriptionsNamespace(Resource[M]):
    """Manage first-class Nozle subscriptions owned by customer Entities."""

    @endpoint
    def ensure(self, customer_id: str, entity_id: str) -> Steps[EntitySubscription]:
        operation = "entity_subscriptions.ensure"
        self._validate_path(customer_id, entity_id, operation)
        payload = yield self._transport.call(
            operation, "PUT", self._entity_path(customer_id, entity_id)
        )
        return cast(EntitySubscription, payload["entity_subscription"])

    @endpoint
    def get(self, customer_id: str, entity_id: str) -> Steps[EntitySubscription]:
        operation = "entity_subscriptions.get"
        self._validate_path(customer_id, entity_id, operation)
        payload = yield self._transport.call(
            operation, "GET", self._entity_path(customer_id, entity_id)
        )
        return cast(EntitySubscription, payload["entity_subscription"])

    @endpoint
    def list(self, customer_id: str) -> Steps[EntitySubscriptionList]:
        operation = "entity_subscriptions.list"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        return cast(
            EntitySubscriptionList,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    f"/api/v1/customers/{path_segment(customer_id)}/entity-subscriptions",
                )
            ),
        )

    @endpoint
    def checkout(
        self,
        customer_id: str,
        entity_id: str,
        *,
        plan_code: str,
        return_url: str | None = None,
        billing_time: str | None = None,
        idempotency_key: str | None = None,
        register_mandate: bool | None = None,
    ) -> Steps[CheckoutResult]:
        return (
            yield from self._change(
                "checkout",
                customer_id,
                entity_id,
                plan_code=plan_code,
                return_url=return_url,
                billing_time=billing_time,
                idempotency_key=idempotency_key,
                register_mandate=register_mandate,
            )
        )

    @endpoint
    def checkout_many(
        self,
        customer_id: str,
        *,
        items: Sequence[EntitySubscriptionCheckoutItem],
        idempotency_key: str,
        return_url: str | None = None,
        billing_time: str | None = None,
    ) -> Steps[EntitySubscriptionCheckoutManyResult]:
        operation = "entity_subscriptions.checkout_many"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        validate_idempotency_key(idempotency_key, operation)
        if len(items) < 1 or len(items) > 100:
            raise NozleValidationError(f"{operation} requires between 1 and 100 items")
        if billing_time is not None and billing_time not in ("calendar", "anniversary"):
            raise NozleValidationError(f"{operation} billing_time must be calendar or anniversary")

        entity_ids: set[str] = set()
        request_items: list[dict[str, str]] = []
        for item in items:
            entity_id = item.get("external_entity_id")
            plan_code = item.get("plan_code")
            if not isinstance(entity_id, str):
                raise NozleValidationError(f"{operation} requires every external_entity_id")
            validate_entity_id(entity_id, operation)
            entity_id = entity_id.strip()
            if entity_id in entity_ids:
                raise NozleValidationError(f"{operation} requires unique external_entity_id values")
            entity_ids.add(entity_id)
            if not isinstance(plan_code, str):
                raise NozleValidationError(f"{operation} requires every plan_code")
            plan_code = require_non_empty(plan_code, "items[].plan_code", operation).strip()
            request_items.append({"external_entity_id": entity_id, "plan_code": plan_code})

        payload = yield self._transport.call(
            operation,
            "POST",
            f"/api/v1/customers/{path_segment(customer_id)}/entity-subscriptions/checkout",
            json_body={
                "entity_subscription_checkout": {
                    "billing_time": billing_time,
                    "return_url": return_url,
                    "items": request_items,
                }
            },
            headers={"Idempotency-Key": idempotency_key},
        )
        return cast(
            EntitySubscriptionCheckoutManyResult,
            payload["entity_subscription_checkout"],
        )

    @endpoint
    def change_plan(
        self,
        customer_id: str,
        entity_id: str,
        *,
        plan_code: str,
        return_url: str | None = None,
        billing_time: str | None = None,
        idempotency_key: str | None = None,
    ) -> Steps[CheckoutResult]:
        return (
            yield from self._change(
                "change-plan",
                customer_id,
                entity_id,
                plan_code=plan_code,
                return_url=return_url,
                billing_time=billing_time,
                idempotency_key=idempotency_key,
            )
        )

    @endpoint
    def cancel(
        self,
        customer_id: str,
        entity_id: str,
        *,
        idempotency_key: str,
        timing: SubscriptionTransitionTiming | None = None,
        credit_action: SubscriptionTransitionCreditAction | None = None,
        refund_mode: SubscriptionTransitionRefundMode | None = None,
        final_invoice_action: SubscriptionTransitionFinalInvoiceAction | None = None,
    ) -> Steps[EntitySubscriptionCancelResult]:
        operation = "entity_subscriptions.cancel"
        self._validate_path(customer_id, entity_id, operation)
        validate_idempotency_key(idempotency_key, operation)
        return cast(
            EntitySubscriptionCancelResult,
            (
                yield self._transport.call(
                    operation,
                    "POST",
                    f"{self._entity_path(customer_id, entity_id)}/cancel",
                    json_body={
                        "timing": timing,
                        "credit_action": credit_action,
                        "refund_mode": refund_mode,
                        "final_invoice_action": final_invoice_action,
                    },
                    headers={"Idempotency-Key": idempotency_key},
                )
            ),
        )

    @endpoint
    def remove(self, customer_id: str, entity_id: str) -> Steps[None]:
        operation = "entity_subscriptions.remove"
        self._validate_path(customer_id, entity_id, operation)
        (
            yield self._transport.call(
                operation,
                "DELETE",
                self._entity_path(customer_id, entity_id),
                expect_json=False,
            )
        )

    def _change(
        self,
        action: str,
        customer_id: str,
        entity_id: str,
        *,
        plan_code: str,
        return_url: str | None = None,
        billing_time: str | None = None,
        idempotency_key: str | None = None,
        register_mandate: bool | None = None,
    ) -> Steps[CheckoutResult]:
        operation = f"entity_subscriptions.{action.replace('-', '_')}"
        self._validate_path(customer_id, entity_id, operation)
        require_non_empty(plan_code, "plan_code", operation)
        if billing_time is not None and billing_time not in ("calendar", "anniversary"):
            raise NozleValidationError(f"{operation} billing_time must be calendar or anniversary")
        headers = None
        if idempotency_key is not None:
            validate_idempotency_key(idempotency_key, operation)
            headers = {"Idempotency-Key": idempotency_key}
        return cast(
            CheckoutResult,
            (
                yield self._transport.call(
                    operation,
                    "POST",
                    f"{self._entity_path(customer_id, entity_id)}/{action}",
                    json_body={
                        "plan_code": plan_code,
                        "return_url": return_url,
                        "billing_time": billing_time,
                        "register_mandate": register_mandate,
                    },
                    headers=headers,
                )
            ),
        )

    def _validate_path(self, customer_id: str, entity_id: str, operation: str) -> None:
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        validate_entity_id(entity_id, operation)

    @staticmethod
    def _entity_path(customer_id: str, entity_id: str) -> str:
        return (
            f"/api/v1/customers/{path_segment(customer_id)}/entities/"
            f"{path_segment(entity_id)}/subscription"
        )
