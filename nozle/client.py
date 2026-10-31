from __future__ import annotations

import os
import re
from datetime import datetime
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Mapping,
    Optional,
    Sequence,
    Union,
    cast,
)
from urllib.parse import quote

import requests

from nozle._operation import Async, M, Resource, Steps, Sync, endpoint, warn_caller
from nozle._transport import AsyncHttpTransport, BaseTransport, HttpTransport
from nozle._validation import (
    path_segment,
    require_catalog_key,
    require_non_empty,
    require_secret_key,
    validate_idempotency_key,
)
from nozle.alerts import AlertsNamespace
from nozle.can import can_steps
from nozle.cost_events import CostEventsNamespace
from nozle.cost_intelligence import CostIntelligenceNamespace
from nozle.credit_notes import CreditNotesNamespace
from nozle.credit_systems import CreditSystemsNamespace
from nozle.credits import CreditsNamespace
from nozle.customers import CustomersNamespace
from nozle.entities import EntitiesNamespace
from nozle.entity_subscriptions import EntitySubscriptionsNamespace
from nozle.errors import NO_ACTIVE_SUBSCRIPTION, NozleAPIError, NozleValidationError
from nozle.events import EventsNamespace
from nozle.invoices import InvoicesNamespace
from nozle.margin import MarginClient
from nozle.plan_versions import PlanVersionsNamespace
from nozle.track import track_steps
from nozle.track_batch import TrackBatchResult, track_batch_steps
from nozle.types import (
    CancellationPolicy,
    CancelSubscriptionResult,
    CanResult,
    CheckAndDeductResult,
    CheckoutResult,
    CheckoutStatus,
    CustomerUpsertResult,
    CustomerUsage,
    JSONMapping,
    ManagePaymentMethodResult,
    PingResult,
    Plan,
    RazorpayVerification,
    SubscribeResult,
    SubscriptionChangePreview,
    SubscriptionOptions,
    SubscriptionTransitionCreditAction,
    SubscriptionTransitionFinalInvoiceAction,
    SubscriptionTransitionParams,
    SubscriptionTransitionPreview,
    SubscriptionTransitionRefundMode,
    SubscriptionTransitionResult,
    TrackBatchEvent,
    WithdrawPendingSubscriptionChangeResult,
)
from nozle.usage import UsageNamespace
from nozle.webhooks import WebhooksNamespace

if TYPE_CHECKING:
    import httpx


def _resolve_api_key(api_key: Optional[str]) -> str:
    if api_key is None:
        api_key = os.environ.get("NOZLE_SECRET_KEY")
    if api_key is None:
        raise ValueError("Nozle: pass api_key or set the NOZLE_SECRET_KEY environment variable")
    return api_key


def _plan_fields(
    operation: str,
    plan_code: Optional[str],
    plan_family_code: Optional[str],
    interval: Optional[str],
    currency: Optional[str],
) -> Dict[str, str]:
    """What to sell: ``plan_code``, or ``plan_family_code`` + ``interval`` (+ ``currency``)."""
    if plan_family_code is None:
        if interval is not None or currency is not None:
            raise NozleValidationError(
                f"{operation} takes interval and currency only with plan_family_code"
            )
        return {"plan_code": require_non_empty(plan_code or "", "plan_code", operation)}
    if plan_code is not None:
        raise NozleValidationError(f"{operation} takes plan_code or plan_family_code, not both")
    fields = {
        "plan_family_code": require_non_empty(plan_family_code, "plan_family_code", operation),
        "interval": require_non_empty(interval or "", "interval", operation),
    }
    if currency:
        fields["currency"] = currency
    return fields


class _BaseNozle(Resource[M]):
    """Everything ``Nozle`` and ``AsyncNozle`` share. Each method is written once and runs on the
    client's transport (see ``nozle._operation``)."""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str],
        events_url: Optional[str],
        timeout: float,
        host: str,
        transport: Callable[[str], BaseTransport[M]],
    ) -> None:
        self.api_key = api_key
        # host fills in whichever of base_url (Engine) and events_url (Core) is not given.
        host = host.rstrip("/")
        self.base_url = (base_url or f"{host}/engine").rstrip("/")
        self.events_url = (events_url or f"{host}/core").rstrip("/")
        self.timeout = timeout
        self._engine = transport(self.base_url)
        self._events = transport(self.events_url)
        super().__init__(self._engine, self.api_key)
        self.margin = MarginClient(
            self.base_url, self.api_key, timeout=self.timeout, _transport=self._engine
        )
        self.events = EventsNamespace()
        self.cost_events = CostEventsNamespace(self._engine, self.api_key)
        self.customers = CustomersNamespace(self._events, self.api_key, self._engine)
        self.cost_intelligence = CostIntelligenceNamespace(self._engine, self.api_key)
        self.credit_systems = CreditSystemsNamespace(self._events, self.api_key)
        self.credits = CreditsNamespace(self._engine, self.api_key)
        self.entities = EntitiesNamespace(self._engine, self.api_key)
        self.entity_subscriptions = EntitySubscriptionsNamespace(self._events, self.api_key)
        self.usage = UsageNamespace(self._engine, self.api_key)
        self.webhooks = WebhooksNamespace(self._events, self.api_key)
        self.invoices = InvoicesNamespace(self._events, self.api_key)
        self.credit_notes = CreditNotesNamespace(self._events, self.api_key)
        self.alerts = AlertsNamespace(self._events, self.api_key)
        self.plan_versions = PlanVersionsNamespace(self._events, self.api_key)
        self._sub_cache: dict[str, str] = {}

    @endpoint
    def track(
        self,
        customer_id: str,
        event: str,
        metadata: Optional[JSONMapping] = None,
        subscription_id: Optional[str] = None,
        transaction_id: Optional[str] = None,
        timestamp: Optional[str] = None,
    ) -> Steps[str]:
        require_secret_key(self.api_key, "track")
        if not subscription_id:
            subscription_id = yield from self._resolve_subscription(customer_id)
        transaction = yield from track_steps(
            self._engine,
            self.api_key,
            customer_id,
            event,
            metadata,
            subscription_id,
            transaction_id,
            timestamp,
        )
        return transaction

    @endpoint
    def track_batch(
        self,
        events: Sequence[TrackBatchEvent],
        *,
        chunk_size: Optional[int] = None,
        concurrency: Optional[int] = None,
    ) -> Steps[TrackBatchResult]:
        """Record many events through Engine's ``/events/batch``.

        The events are split into gzip requests of ``chunk_size`` (1 to 5000, the Engine maximum;
        default 5000) sent ``concurrency`` at a time (1 to 64, default 4). A request above a
        lower Engine cap is split and resent. The result has one
        entry per event: ``accepted``, ``rejected`` (with Engine's per-field codes) or ``failed``
        (with the error of its request). Raises only for invalid arguments; a failed request
        never hides the others' results. Engine resolves each customer's subscription; this call
        makes no lookup.
        """
        return (
            yield from track_batch_steps(
                self._engine, self.api_key, events, chunk_size, concurrency
            )
        )

    @endpoint
    def can(
        self,
        customer_id: str,
        feature: str,
        metadata: Optional[Mapping[str, Union[str, int, float, bool]]] = None,
        *,
        subscription_id: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> Steps[CanResult]:
        """Whether the customer may use the feature now.

        A customer with several subscriptions needs ``subscription_id`` or ``entity_id``
        (external ids); without one the result is ``allowed: False``,
        ``reason: "subscription_required"``.
        """
        return (
            yield from can_steps(
                self._engine,
                self.api_key,
                customer_id,
                feature,
                metadata,
                subscription_id=subscription_id,
                entity_id=entity_id,
            )
        )

    @endpoint
    def plans(
        self, *, currency: Optional[str] = None, external_customer_id: Optional[str] = None
    ) -> Steps[list[Plan]]:
        """The plan catalog. Without ``currency`` a secret key gets every plan in every currency;
        ``currency`` keeps that currency only (an unknown one is a 422), and
        ``external_customer_id`` (secret key only) the customer's locked currency, ignored when the
        organization allows several currencies per customer. A publishable key without
        ``currency`` gets each family's default-currency plans. A family plan carries ``family``
        and ``prices`` (every variant)."""
        require_catalog_key(self.api_key)
        query = {"currency": currency, "external_customer_id": external_customer_id}
        params = {key: value for key, value in query.items() if value}
        payload = yield self._engine.call("plans", "GET", "/api/v1/plans", params=params or None)
        if not isinstance(payload, Mapping):
            raise NozleAPIError("plans", 200, "response was not a JSON object")
        plans = payload.get("plans")
        if plans is None:
            plans = []
        if not isinstance(plans, list):
            raise NozleAPIError("plans", 200, "plans was not a list")
        return cast(list[Plan], plans)

    @endpoint
    def checkout(
        self,
        customer_id: str,
        plan_code: Optional[str] = None,
        return_url: Optional[str] = None,
        *,
        plan_family_code: Optional[str] = None,
        interval: Optional[str] = None,
        currency: Optional[str] = None,
        success_url: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        register_mandate: Optional[bool] = None,
        external_entity_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
        quote_id: Optional[str] = None,
        cancel_url: Optional[str] = None,
    ) -> Steps[CheckoutResult]:
        operation = "checkout"
        require_secret_key(self.api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        plan = _plan_fields(operation, plan_code, plan_family_code, interval, currency)
        if return_url is not None and success_url is not None and return_url != success_url:
            raise NozleValidationError(
                "checkout received conflicting return_url and deprecated success_url values"
            )
        resolved_return_url = return_url if return_url is not None else success_url
        if success_url is not None:
            warn_caller("success_url is deprecated; use return_url", DeprecationWarning)
        body: dict[str, Any] = {**plan, "customer_id": customer_id}
        if subscription_id is not None:
            require_non_empty(subscription_id, "subscription_id", operation)
            body["subscription_id"] = subscription_id
        if quote_id is not None:
            require_non_empty(quote_id, "quote_id", operation)
            if not subscription_id:
                raise NozleValidationError("checkout quote_id requires an explicit subscription_id")
            body["quote_id"] = quote_id
        if resolved_return_url:
            body["return_url"] = resolved_return_url
        if cancel_url:
            body["cancel_url"] = cancel_url
        try:
            return cast(
                CheckoutResult,
                (
                    yield from self._checkout_request(
                        operation,
                        body,
                        idempotency_key=idempotency_key,
                        register_mandate=register_mandate,
                        external_entity_id=external_entity_id,
                    )
                ),
            )
        finally:
            self._sub_cache.pop(customer_id, None)  # the active subscription may have changed

    @endpoint
    def checkout_invoice(
        self,
        invoice_id: str,
        *,
        idempotency_key: Optional[str] = None,
        register_mandate: Optional[bool] = None,
        cancel_url: Optional[str] = None,
    ) -> Steps[CheckoutResult]:
        require_secret_key(self.api_key, "checkout_invoice")
        require_non_empty(invoice_id, "invoice_id", "checkout_invoice")
        return cast(
            CheckoutResult,
            (
                yield from self._checkout_request(
                    "checkout_invoice",
                    {
                        "invoice_id": invoice_id,
                        **({"cancel_url": cancel_url} if cancel_url else {}),
                    },
                    idempotency_key=idempotency_key,
                    register_mandate=register_mandate,
                )
            ),
        )

    @endpoint
    def manage_payment_method(
        self,
        customer_id: str,
        return_url: Optional[str] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Steps[ManagePaymentMethodResult]:
        """Return a Stripe billing page `url` where the customer adds or replaces a payment method.

        Stripe only. For a Razorpay customer (who pays each invoice by payment link) this
        raises `NozleAPIError` with HTTP 422 and `has_code("razorpay_pays_by_link")`.
        """
        operation = "manage_payment_method"
        require_secret_key(self.api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        headers = None
        if idempotency_key is not None:
            validate_idempotency_key(idempotency_key, operation)
            headers = {"Idempotency-Key": idempotency_key}
        payload = yield self._engine.call(
            operation,
            "POST",
            f"/api/v1/customers/{quote(customer_id, safe='')}/payment-method/manage",
            json_body={"return_url": return_url} if return_url else None,
            headers=headers,
        )
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("manage_payment_method"), Mapping
        ):
            raise NozleAPIError(operation, 200, "response had no manage_payment_method")
        return cast(ManagePaymentMethodResult, payload["manage_payment_method"])

    @endpoint
    def verify_checkout(
        self,
        checkout_id: str,
        verification: RazorpayVerification,
        *,
        customer_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> Steps[CheckoutStatus]:
        operation = "verify_checkout"
        require_secret_key(self.api_key, operation)
        require_non_empty(checkout_id, "checkout_id", operation)
        for field in ("razorpay_order_id", "razorpay_payment_id", "razorpay_signature"):
            value = verification.get(field, "")
            if not isinstance(value, str):
                raise NozleValidationError(f"{operation} requires {field}")
            require_non_empty(value, field, operation)
        return cast(
            CheckoutStatus,
            (
                yield self._engine.call(
                    operation,
                    "POST",
                    f"/api/v1/checkout/{quote(checkout_id, safe='')}/verify",
                    json_body=verification,
                    params=self._checkout_scope(customer_id, subscription_id),
                )
            ),
        )

    @endpoint
    def checkout_status(
        self,
        checkout_id: str,
        *,
        customer_id: Optional[str] = None,
        subscription_id: Optional[str] = None,
    ) -> Steps[CheckoutStatus]:
        """Status of a paid upgrade (pass its ``plan_upgrade_intent_id`` with the customer and
        subscription) or of a Razorpay operation. Not a Stripe ``checkout_id`` (``cs_...``): a
        new-subscription or invoice Stripe checkout has no status call, so confirm it through
        webhooks or by reloading billing state."""
        operation = "checkout_status"
        require_secret_key(self.api_key, operation)
        require_non_empty(checkout_id, "checkout_id", operation)
        return cast(
            CheckoutStatus,
            (
                yield self._engine.call(
                    operation,
                    "GET",
                    f"/api/v1/checkout/{quote(checkout_id, safe='')}",
                    params=self._checkout_scope(customer_id, subscription_id),
                )
            ),
        )

    def _checkout_scope(
        self, customer_id: Optional[str], subscription_id: Optional[str]
    ) -> Optional[dict[str, str]]:
        if customer_id is None and subscription_id is None:
            return None
        require_non_empty(customer_id or "", "customer_id", "checkout scope")
        require_non_empty(subscription_id or "", "subscription_id", "checkout scope")
        return {"customer_id": customer_id or "", "subscription_id": subscription_id or ""}

    @endpoint
    def subscription_options(
        self, customer_id: str, subscription_id: str
    ) -> Steps[SubscriptionOptions]:
        require_secret_key(self.api_key, "subscription_options")
        return cast(
            SubscriptionOptions,
            (
                yield self._engine.call(
                    "subscription_options",
                    "GET",
                    "/api/v1/subscriptions/options",
                    params=self._checkout_scope(customer_id, subscription_id),
                )
            ),
        )

    @endpoint
    def preview_subscription_change(
        self, customer_id: str, subscription_id: str, plan_code: str
    ) -> Steps[SubscriptionChangePreview]:
        require_secret_key(self.api_key, "preview_subscription_change")
        scope = self._checkout_scope(customer_id, subscription_id) or {}
        require_non_empty(plan_code, "plan_code", "preview_subscription_change")
        return cast(
            SubscriptionChangePreview,
            (
                yield self._engine.call(
                    "preview_subscription_change",
                    "POST",
                    "/api/v1/subscriptions/preview",
                    json_body={**scope, "plan_code": plan_code},
                )
            ),
        )

    @endpoint
    def withdraw_pending_subscription_change(
        self,
        customer_id: str,
        subscription_id: str,
        pending_subscription_id: str,
        *,
        idempotency_key: str,
    ) -> Steps[WithdrawPendingSubscriptionChangeResult]:
        operation = "withdraw_pending_subscription_change"
        require_secret_key(self.api_key, operation)
        scope = self._checkout_scope(customer_id, subscription_id) or {}
        require_non_empty(pending_subscription_id, "pending_subscription_id", operation)
        validate_idempotency_key(idempotency_key, operation)
        return cast(
            WithdrawPendingSubscriptionChangeResult,
            (
                yield self._engine.call(
                    operation,
                    "POST",
                    "/api/v1/subscriptions/transitions/withdraw",
                    json_body={**scope, "pending_subscription_id": pending_subscription_id},
                    headers={"Idempotency-Key": idempotency_key},
                )
            ),
        )

    def _checkout_request(
        self,
        operation: str,
        body: dict[str, Any],
        *,
        idempotency_key: Optional[str] = None,
        register_mandate: Optional[bool] = None,
        external_entity_id: Optional[str] = None,
    ) -> Steps[Any]:
        headers = None
        if idempotency_key is not None:
            validate_idempotency_key(idempotency_key, operation)
            headers = {"Idempotency-Key": idempotency_key}
        if register_mandate is not None:
            if type(register_mandate) is not bool:
                raise NozleValidationError("register_mandate must be a boolean")
            body["register_mandate"] = register_mandate
        if external_entity_id is not None:
            body["external_entity_id"] = external_entity_id
        return (
            yield self._engine.call(
                operation, "POST", "/api/v1/checkout", json_body=body, headers=headers
            )
        )

    @endpoint
    def subscribe(
        self,
        customer_id: str,
        plan_code: Optional[str] = None,
        *,
        plan_version: Optional[int] = None,
        plan_family_code: Optional[str] = None,
        interval: Optional[str] = None,
        currency: Optional[str] = None,
    ) -> Steps[SubscribeResult]:
        """Subscribe the customer to the current version of the plan, or to ``plan_version``
        (for example to keep selling an older price). The same customer and code again keep the
        subscription's own version; another version of an existing subscription raises a 422
        ``plan_version_change_not_supported``. ``plan_family_code`` with ``interval`` (and
        optional ``currency``) replaces ``plan_code``: Core picks the family's variant."""
        operation = "subscribe"
        require_secret_key(self.api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        plan = _plan_fields(operation, plan_code, plan_family_code, interval, currency)
        body: Dict[str, Any] = {**plan, "customer_id": customer_id}
        if plan_version is not None:
            if (
                isinstance(plan_version, bool)
                or not isinstance(plan_version, int)
                or plan_version < 1
            ):
                raise NozleValidationError(
                    f"{operation} requires plan_version to be an integer of 1 or more"
                )
            body["plan_version"] = plan_version
        try:
            return cast(
                SubscribeResult,
                (
                    yield self._engine.call(
                        operation,
                        "POST",
                        "/api/v1/subscribe",
                        json_body=body,
                    )
                ),
            )
        finally:
            self._sub_cache.pop(customer_id, None)

    @endpoint
    def cancel_subscription(
        self,
        customer_id: str,
        subscription_id: str,
        policy: Optional[CancellationPolicy] = None,
        *,
        credit_action: Optional[SubscriptionTransitionCreditAction] = None,
        refund_mode: Optional[SubscriptionTransitionRefundMode] = None,
        final_invoice_action: Optional[SubscriptionTransitionFinalInvoiceAction] = None,
    ) -> Steps[CancelSubscriptionResult]:
        """Cancel a subscription. Options left as None are not sent, so the subscription, then the
        organization's cancellation settings decide them (timing included)."""
        operation = "cancel_subscription"
        require_secret_key(self.api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        require_non_empty(subscription_id, "subscription_id", operation)
        if policy is not None and policy not in ("end_of_period", "immediate"):
            raise NozleValidationError(
                "cancel_subscription policy must be 'end_of_period' or 'immediate'"
            )
        options = {
            "cancellation_policy": policy,
            "credit_action": credit_action,
            "refund_mode": refund_mode,
            "final_invoice_action": final_invoice_action,
        }

        try:
            return cast(
                CancelSubscriptionResult,
                (
                    yield self._engine.call(
                        operation,
                        "DELETE",
                        f"/api/v1/subscriptions/{quote(subscription_id, safe='')}",
                        params={
                            "customer_id": customer_id,
                            **{k: v for k, v in options.items() if v is not None},
                        },
                    )
                ),
            )
        finally:
            self._sub_cache.pop(customer_id, None)

    @endpoint
    def preview_subscription_transition(
        self,
        params: SubscriptionTransitionParams,
    ) -> Steps[SubscriptionTransitionPreview]:
        operation = "preview_subscription_transition"
        body = self._subscription_transition_body(params, operation)
        return cast(
            SubscriptionTransitionPreview,
            (
                yield self._engine.call(
                    operation,
                    "POST",
                    "/api/v1/subscriptions/transitions/preview",
                    json_body=body,
                    idempotent=True,
                )
            ),
        )

    @endpoint
    def apply_subscription_transition(
        self,
        params: SubscriptionTransitionParams,
        *,
        idempotency_key: str,
    ) -> Steps[SubscriptionTransitionResult]:
        operation = "apply_subscription_transition"
        body = self._subscription_transition_body(params, operation)
        require_non_empty(idempotency_key, "idempotency_key", operation)
        if len(idempotency_key.encode("utf-8")) > 255:
            raise NozleValidationError(
                "apply_subscription_transition idempotency_key must not exceed 255 bytes"
            )
        try:
            return cast(
                SubscriptionTransitionResult,
                (
                    yield self._engine.call(
                        operation,
                        "POST",
                        "/api/v1/subscriptions/transitions",
                        json_body=body,
                        headers={"Idempotency-Key": idempotency_key},
                    )
                ),
            )
        finally:
            self._sub_cache.pop(str(body["customer_id"]), None)

    def _subscription_transition_body(
        self,
        params: SubscriptionTransitionParams,
        operation_name: str,
    ) -> JSONMapping:
        require_secret_key(self.api_key, operation_name)
        customer_id = str(params.get("customer_id", "")).strip()
        subscription_id = str(params.get("subscription_id", "")).strip()
        require_non_empty(customer_id, "customer_id", operation_name)
        require_non_empty(subscription_id, "subscription_id", operation_name)

        transition_operation = params.get("operation")
        timing = params.get("timing")
        target_plan_code = str(params.get("target_plan_code", "")).strip()
        billing_anchor = params.get("billing_anchor")
        proration_behavior = params.get("proration_behavior")
        credit_action = params.get("credit_action")
        refund_mode = params.get("refund_mode")
        final_invoice_action = params.get("final_invoice_action")
        expected_effective_at = params.get("expected_effective_at")
        quote_id = params.get("quote_id")
        if quote_id is not None:
            require_non_empty(quote_id, "quote_id", operation_name)
            if transition_operation != "downgrade":
                raise NozleValidationError("quote_id requires a downgrade transition")
        if expected_effective_at is not None:
            if transition_operation != "cancel" or timing != "end_of_period":
                raise NozleValidationError(
                    "expected_effective_at requires end_of_period cancellation"
                )
            try:
                if not isinstance(expected_effective_at, str) or not re.fullmatch(
                    r"\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)", expected_effective_at
                ):
                    raise ValueError
                datetime.fromisoformat(expected_effective_at.replace("Z", "+00:00"))
            except ValueError:
                raise NozleValidationError(
                    "expected_effective_at requires an ISO timestamp with timezone"
                ) from None
        if transition_operation not in ("cancel", "downgrade", "uncancel"):
            raise NozleValidationError("operation must be 'cancel', 'downgrade', or 'uncancel'")
        if timing is not None and timing not in ("end_of_period", "immediate"):
            raise NozleValidationError("timing must be 'end_of_period' or 'immediate'")
        if billing_anchor is not None and billing_anchor not in ("keep_anchor", "reset_anchor"):
            raise NozleValidationError("billing_anchor must be 'keep_anchor' or 'reset_anchor'")
        if proration_behavior is not None and proration_behavior not in (
            "prorate_immediately",
            "none",
        ):
            raise NozleValidationError("proration_behavior must be 'prorate_immediately' or 'none'")
        if credit_action is not None and credit_action not in (
            "credit",
            "refund",
            "offset",
            "none",
        ):
            raise NozleValidationError(
                "credit_action must be 'credit', 'refund', 'offset', or 'none'"
            )
        if refund_mode is not None and refund_mode not in ("prorated", "full"):
            raise NozleValidationError("refund_mode must be 'prorated' or 'full'")
        if final_invoice_action is not None and final_invoice_action not in ("generate", "skip"):
            raise NozleValidationError("final_invoice_action must be 'generate' or 'skip'")
        if transition_operation in ("cancel", "uncancel") and target_plan_code:
            raise NozleValidationError(
                "target_plan_code is forbidden for cancellation and uncancel"
            )
        if transition_operation == "downgrade" and not target_plan_code:
            raise NozleValidationError("target_plan_code is required for downgrade")
        if timing == "end_of_period" and credit_action not in (None, "none"):
            raise NozleValidationError("end_of_period transitions require credit_action 'none'")
        if refund_mode == "full" and credit_action != "refund":
            raise NozleValidationError("full refund_mode requires credit_action 'refund'")
        if transition_operation == "uncancel" and any(
            value is not None
            for value in (
                timing,
                billing_anchor,
                proration_behavior,
                credit_action,
                refund_mode,
                final_invoice_action,
            )
        ):
            raise NozleValidationError("uncancel does not accept settlement options")

        payload: dict[str, Any] = {
            "customer_id": customer_id,
            "subscription_id": subscription_id,
            "operation": transition_operation,
        }
        optional_values = {
            "timing": timing,
            "target_plan_code": target_plan_code or None,
            "billing_anchor": billing_anchor,
            "proration_behavior": proration_behavior,
            "credit_action": credit_action,
            "refund_mode": refund_mode,
            "final_invoice_action": final_invoice_action,
            "expected_effective_at": expected_effective_at,
            "quote_id": quote_id,
        }
        payload.update({key: value for key, value in optional_values.items() if value is not None})
        return payload

    def wrap_http_client(self, client: Any, **options: Any) -> Any:
        """Make an existing httpx or httpx2 client record the token usage of every LLM response.

        Pass the client your LLM SDK takes as ``http_client``, for example
        ``anthropic.DefaultHttpxClient()``. See :func:`nozle.integrations.capture.wrap_http_client`.
        """
        from nozle.integrations.capture import wrap_http_client

        return wrap_http_client(client, cast(Any, self), **options)

    def capture_async_http_client(self, **options: Any) -> Any:
        """Return a new ``httpx.AsyncClient`` that records LLM token usage, for ``AsyncOpenAI``
        and the like. See :func:`nozle.integrations.capture.capture_async_http_client`."""
        from nozle.integrations.capture import capture_async_http_client

        return capture_async_http_client(cast(Any, self), **options)

    @endpoint
    def record_usage(
        self,
        customer_id: str,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
        feature_code: str = "llm_tokens",
        provider: str = "",
        request_id: Optional[str] = None,
        latency_ms: int = 0,
        feature: Optional[str] = None,
        cost_meter_code: Optional[str] = None,
    ) -> Steps[None]:
        """Record one LLM call's token usage that your code read itself.

        ``input_tokens`` counts all input, including cached input and cache writes;
        ``output_tokens`` counts all output, including reasoning. Recording the same
        ``request_id`` twice records one event. Raises the first delivery error.
        """
        from nozle.integrations._common import (
            ProviderTokenUsage,
            capture_usage_steps,
            compact_categories,
            token_count,
        )

        require_non_empty(customer_id, "customer_id", "record_usage")
        total_input = token_count(input_tokens)
        total_output = token_count(output_tokens)
        cached = min(total_input, token_count(cached_input_tokens))
        cache_write = min(total_input - cached, token_count(cache_write_tokens))
        reasoning = min(total_output, token_count(reasoning_tokens))
        usage = ProviderTokenUsage(
            feature_input_tokens=total_input,
            feature_output_tokens=total_output,
            categories=compact_categories(
                (
                    ("input", total_input - cached - cache_write),
                    ("cached_input", cached),
                    ("cache_write", cache_write),
                    ("output", total_output - reasoning),
                    ("reasoning", reasoning),
                )
            ),
        )
        errors = yield from capture_usage_steps(
            self,
            customer_id,
            feature_code,
            feature=feature,
            cost_meter_code=cost_meter_code,
            provider=provider,
            model=model,
            request_id=request_id,
            usage=usage,
            latency_ms=token_count(latency_ms),
        )
        if errors:
            raise errors[0]

    @endpoint
    def ping(self) -> Steps[PingResult]:
        require_secret_key(self.api_key, "ping")
        return cast(PingResult, (yield self._engine.call("ping", "GET", "/api/v1/ping")))

    @endpoint
    def check_and_deduct(
        self,
        customer_id: str,
        feature: Union[str, int, float, None] = None,
        credits: Union[int, float, None] = None,
    ) -> Steps[CheckAndDeductResult]:
        """Deduct credits from the customer's wallet (legacy wallet adapter).

        Call it as ``check_and_deduct(customer_id, credits=5)``. ``feature`` is
        deprecated and ignored: it is not sent, so old calls such as
        ``check_and_deduct(customer_id, "completion", 5)`` keep working.
        """
        operation = "check_and_deduct"
        require_secret_key(self.api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        # check_and_deduct("c", 5): the number is the credits, not a feature name.
        if credits is None and isinstance(feature, (int, float)) and not isinstance(feature, bool):
            credits = feature
        if credits is None or isinstance(credits, bool) or not isinstance(credits, (int, float)):
            raise NozleValidationError(f"{operation} requires credits")
        return cast(
            CheckAndDeductResult,
            (
                yield self._engine.call(
                    operation,
                    "POST",
                    "/api/v1/check-and-deduct",
                    json_body={"customer_id": customer_id, "credits": credits},
                )
            ),
        )

    @endpoint
    def current_usage(
        self, customer_id: str, subscription_id: Optional[str] = None
    ) -> Steps[CustomerUsage]:
        """Usage of the current billing period. Without ``subscription_id``, the one active
        subscription is used."""
        payload = yield from self._usage_read("current_usage", customer_id, subscription_id)
        return cast(CustomerUsage, payload["customer_usage"])

    @endpoint
    def past_usage(
        self,
        customer_id: str,
        subscription_id: Optional[str] = None,
        *,
        periods: Optional[int] = None,
    ) -> Steps[List[CustomerUsage]]:
        """Usage of past billing periods, newest first. ``periods`` limits how many."""
        payload = yield from self._usage_read(
            "past_usage",
            customer_id,
            subscription_id,
            {"periods_count": periods} if periods is not None else {},
        )
        return cast(List[CustomerUsage], payload.get("usage_periods") or [])

    def _usage_read(
        self,
        route: str,
        customer_id: str,
        subscription_id: Optional[str],
        params: Optional[Dict[str, Any]] = None,
    ) -> Steps[Mapping[str, Any]]:
        operation = route
        require_secret_key(self.api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        subscription = subscription_id or (yield from self._resolve_subscription(customer_id))
        payload = yield self._events.call(
            operation,
            "GET",
            f"/api/v1/customers/{path_segment(customer_id)}/{route}",
            params={"external_subscription_id": subscription, **(params or {})},
        )
        if not isinstance(payload, Mapping):
            raise NozleAPIError(operation, 200, "response was not a JSON object")
        return payload

    def _resolve_subscription(self, customer_id: str) -> Steps[str]:
        operation = "track.subscription_lookup"
        require_secret_key(self.api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        cached = self._sub_cache.get(customer_id)
        if cached:
            return cached

        payload = yield self._events.call(
            operation,
            "GET",
            "/api/v1/subscriptions",
            params={"external_customer_id": customer_id, "status[]": "active"},
        )
        if not isinstance(payload, Mapping):
            raise NozleAPIError(operation, 200, "response was not a JSON object")
        subscriptions = payload.get("subscriptions")
        if subscriptions is None:
            subscriptions = []
        if not isinstance(subscriptions, list):
            raise NozleAPIError(operation, 200, "subscriptions was not a list")
        if len(subscriptions) == 0:
            raise NozleAPIError(
                operation,
                422,
                {
                    "code": NO_ACTIVE_SUBSCRIPTION,
                    "error": f"no active subscription for customer {customer_id!r}",
                },
            )
        if len(subscriptions) > 1:
            raise NozleAPIError(
                operation,
                422,
                {
                    "code": "active_subscription_ambiguous",
                    "error": (
                        f"customer {customer_id!r} has {len(subscriptions)} active subscriptions; "
                        "specify subscription_id"
                    ),
                },
            )
        subscription = subscriptions[0]
        if not isinstance(subscription, Mapping) or not isinstance(
            subscription.get("external_id"), str
        ):
            raise NozleAPIError(operation, 200, "subscription response was incomplete")
        external_id = cast(str, subscription["external_id"])
        self._sub_cache[customer_id] = external_id
        return external_id


class Nozle(_BaseNozle[Sync]):
    """Synchronous backend SDK for Nozle Engine and Core APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        events_url: Optional[str] = None,
        timeout: float = 10,
        *,
        host: str = "https://api.nozle.app",
        max_retries: int = 2,
        retry_delay: float = 0.5,
        _session: Optional[requests.Session] = None,
    ) -> None:
        api_key = _resolve_api_key(api_key)
        self._session = _session or requests.Session()
        self._owns_session = _session is None
        session = self._session
        super().__init__(
            api_key,
            base_url,
            events_url,
            timeout,
            host,
            lambda url: HttpTransport(
                url,
                api_key,
                timeout=timeout,
                session=session,
                max_retries=max_retries,
                retry_delay=retry_delay,
            ),
        )

    def capture_http_client(self, **options: Any) -> Any:
        """Return a new httpx client that records LLM token usage (``module="httpx2"`` for httpx2).

        See :func:`nozle.integrations.capture.capture_http_client`.
        """
        from nozle.integrations.capture import capture_http_client

        return capture_http_client(self, **options)

    def close(self) -> None:
        if self._owns_session:
            self._session.close()

    def __enter__(self) -> Nozle:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()


class AsyncNozle(_BaseNozle[Async]):
    """Asyncio backend SDK for Nozle Engine and Core APIs: the methods of ``Nozle``, awaited.

    Requests go through ``httpx.AsyncClient`` (``pip install 'nozle-sdk[httpx]'``). Use it as
    ``async with AsyncNozle() as nozle:`` or call ``await nozle.aclose()`` when done.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        events_url: Optional[str] = None,
        timeout: float = 10,
        *,
        host: str = "https://api.nozle.app",
        max_retries: int = 2,
        retry_delay: float = 0.5,
        _client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        api_key = _resolve_api_key(api_key)
        try:
            import httpx
        except ImportError as error:
            raise ImportError("AsyncNozle needs httpx: pip install 'nozle-sdk[httpx]'") from error
        self._client = _client or httpx.AsyncClient()
        self._owns_client = _client is None
        client = self._client
        super().__init__(
            api_key,
            base_url,
            events_url,
            timeout,
            host,
            lambda url: AsyncHttpTransport(
                url,
                api_key,
                timeout=timeout,
                client=client,
                max_retries=max_retries,
                retry_delay=retry_delay,
            ),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> AsyncNozle:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.aclose()


__all__ = ["Nozle", "AsyncNozle", "CustomerUpsertResult"]
