from __future__ import annotations

import time
from typing import Any, Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._transport import EXPLICIT_NULL, BaseTransport
from nozle._validation import (
    path_segment,
    require_non_empty,
    require_secret_key,
    validate_idempotency_key,
)
from nozle.errors import NozleValidationError
from nozle.types import (
    BillingStateImport,
    BillingStateImportRequest,
    CreditBalanceImport,
    CreditBalanceImportRequest,
    CustomerAddress,
    CustomerBillingConfiguration,
    CustomerTaxId,
    CustomerType,
    CustomerUpsertResult,
)


def _address_to_wire(address: CustomerAddress) -> dict[str, object]:
    """Core's flat keys: ``address_line1``, ``zipcode`` and so on."""
    keys = {"line1": "address_line1", "line2": "address_line2", "postal_code": "zipcode"}
    return {keys.get(key, key): value for key, value in address.items() if value is not None}


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
        *,
        customer_type: CustomerType | None = None,
        legal_name: str | None = None,
        address: CustomerAddress | None = None,
        shipping_address: CustomerAddress | None = None,
        tax_id: CustomerTaxId | None = None,
        clear_tax_id: bool = False,
        tax_exempt: bool | None = None,
    ) -> Steps[CustomerUpsertResult]:
        """Create or update a customer. ``tax_id`` is ``{"type": "eu_vat", "value": "..."}``;
        ``clear_tax_id=True`` removes the stored one. Core validates the format. The result
        carries ``tax_identification_status`` and ``tax_treatment``; a VIES check can leave
        the status ``pending``, so call ``wait_for_tax_check`` before a checkout."""
        operation = "customers.upsert"
        require_secret_key(self._api_key, operation)
        require_non_empty(external_id, "external_id", operation)
        customer: dict[str, object] = {"external_id": external_id}
        if name is not None:
            customer["name"] = name
        if email is not None:
            customer["email"] = email
        if customer_type is not None:
            customer["customer_type"] = customer_type
        if legal_name is not None:
            customer["legal_name"] = legal_name
        if address is not None:
            customer.update(_address_to_wire(address))
        if shipping_address is not None:
            customer["shipping_address"] = _address_to_wire(shipping_address)
        if tax_exempt is not None:
            customer["tax_exempt"] = tax_exempt
        if tax_id is not None and clear_tax_id:
            raise NozleValidationError(f"{operation} takes tax_id or clear_tax_id, not both")
        if tax_id is not None:
            # Mirrors Core's "type needs a value"; the format is Core's to judge.
            require_non_empty(tax_id.get("type", ""), "tax_id.type", operation)
            require_non_empty(tax_id.get("value", ""), "tax_id.value", operation)
            customer["tax_identification_number"] = tax_id["value"]
            customer["tax_identification_type"] = tax_id["type"]
        elif clear_tax_id:
            customer["tax_identification_number"] = EXPLICIT_NULL
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
    def get(self, external_id: str) -> Steps[CustomerUpsertResult]:
        """One customer with its tax fields (``GET /customers/:external_id``). Secret key."""
        return (yield from self._get(external_id))

    def _get(self, external_id: str) -> Steps[CustomerUpsertResult]:
        operation = "customers.get"
        require_secret_key(self._api_key, operation)
        require_non_empty(external_id, "external_id", operation)
        payload = yield self._transport.call(
            operation, "GET", f"/api/v1/customers/{path_segment(external_id)}"
        )
        return cast(CustomerUpsertResult, payload["customer"])

    @endpoint
    def wait_for_tax_check(
        self, external_id: str, *, timeout: float = 10.0, interval: float = 0.5
    ) -> Steps[CustomerUpsertResult]:
        """Poll ``get`` until ``tax_identification_status`` is not ``pending`` (a VIES check is
        running) and return the customer. Call it before a checkout so the price already
        reflects reverse charge. Raises ``NozleValidationError`` after ``timeout`` seconds;
        the check keeps running in Core."""
        deadline = time.monotonic() + timeout
        while True:
            customer = yield from self._get(external_id)
            if customer.get("tax_identification_status") != "pending":
                return customer
            if time.monotonic() + interval > deadline:
                raise NozleValidationError(
                    f"customers.wait_for_tax_check timed out after {timeout:g} s: "
                    "tax check still pending"
                )
            yield self._transport.pause(interval)

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
