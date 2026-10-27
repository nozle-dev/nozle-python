from __future__ import annotations

from typing import Any, Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._transport import BaseTransport
from nozle._validation import (
    path_segment,
    require_non_empty,
    require_secret_key,
    validate_idempotency_key,
)
from nozle.types import (
    BillingStateImport,
    BillingStateImportRequest,
    CreditBalanceImport,
    CreditBalanceImportRequest,
    CustomerBillingConfiguration,
    CustomerUpsertResult,
)


class CustomersNamespace(Resource[M]):
    """Create or update customers through the backend-only API."""

    def __init__(
        self,
        transport: BaseTransport[M],
        api_key: str,
        engine: Optional[BaseTransport[M]] = None,
    ) -> None:
        super().__init__(transport, api_key)
        # Core serves customers and billing-state imports; the Engine serves credit-balance imports.
        self._engine = engine or transport

    @endpoint
    def upsert(
        self,
        external_id: str,
        name: str | None = None,
        email: str | None = None,
        billing_configuration: CustomerBillingConfiguration | None = None,
    ) -> Steps[CustomerUpsertResult]:
        operation = "customers.upsert"
        require_secret_key(self._api_key, operation)
        require_non_empty(external_id, "external_id", operation)
        customer: dict[str, object] = {"external_id": external_id}
        if name is not None:
            customer["name"] = name
        if email is not None:
            customer["email"] = email
        if billing_configuration is not None:
            require_non_empty(
                billing_configuration.get("payment_provider", ""),
                "billing_configuration.payment_provider",
                operation,
            )
            customer["billing_configuration"] = {
                key: value for key, value in billing_configuration.items() if value is not None
            }
        payload = yield self._transport.call(
            operation,
            "POST",
            "/api/v1/customers",
            json_body={"customer": customer},
            idempotent=True,
        )
        return cast(CustomerUpsertResult, payload["customer"])

    @endpoint
    def import_credit_balance(
        self,
        customer_id: str,
        body: CreditBalanceImportRequest,
        *,
        idempotency_key: str,
    ) -> Steps[CreditBalanceImport]:
        """Record the opening credit balance of a subscription imported with
        ``import_billing_state``, without an invoice or usage event (Engine). Retry an
        uncertain call with the same ``idempotency_key`` and body."""
        payload = yield from self._import(
            "customers.import_credit_balance",
            self._engine,
            "credit-balance-imports",
            customer_id,
            body,
            idempotency_key,
        )
        return cast(CreditBalanceImport, payload["credit_balance_import"])

    @endpoint
    def import_billing_state(
        self,
        customer_id: str,
        body: BillingStateImportRequest,
        *,
        idempotency_key: str,
    ) -> Steps[BillingStateImport]:
        """Import a subscription another system already bills (``billing_authority:
        external``), without an invoice, checkout or payment (Core). Retry an uncertain call
        with the same ``idempotency_key`` and body."""
        payload = yield from self._import(
            "customers.import_billing_state",
            self._transport,
            "billing-state-imports",
            customer_id,
            {"billing_state_import": body},
            idempotency_key,
        )
        return cast(BillingStateImport, payload["billing_state_import"])

    def _import(
        self,
        operation: str,
        transport: BaseTransport[M],
        route: str,
        customer_id: str,
        body: Mapping[str, Any],
        idempotency_key: str,
    ) -> Steps[Any]:
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        validate_idempotency_key(idempotency_key, operation)
        return (
            yield transport.call(
                operation,
                "POST",
                f"/api/v1/customers/{path_segment(customer_id)}/{route}",
                json_body=body,
                headers={"Idempotency-Key": idempotency_key},
            )
        )
