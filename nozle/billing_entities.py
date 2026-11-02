"""Billing entities: the legal entities that issue invoices, with their tax settings (Core)."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.errors import NozleAPIError, NozleValidationError
from nozle.types import BillingEntity, BillingEntityPage, TaxProvider

TAX_PROVIDERS = ("nozle", "stripe_tax")

#: Fields Core accepts on create; ``update()`` takes the same, minus ``code``, plus
#: ``tax_codes`` and ``invoice_custom_section_codes``.
FIELDS = (
    "einvoicing",
    "email",
    "legal_name",
    "legal_number",
    "tax_identification_number",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "zipcode",
    "country",
    "default_currency",
    "timezone",
    "document_numbering",
    "document_number_prefix",
    "finalize_zero_amount_invoice",
    "net_payment_term",
    "eu_tax_management",
    "customer_location_required",
    "logo",
    "email_settings",
    "billing_configuration",
)
UPDATE_ONLY = ("tax_codes", "invoice_custom_section_codes")


class BillingEntitiesNamespace(Resource[M]):
    """Billing entities hold the legal identity, numbering and tax settings of an invoice issuer.

    ``tax_provider`` is ``"nozle"`` (Nozle computes tax from your tax rates) or ``"stripe_tax"``
    (Stripe Tax computes it; needs a connected Stripe account). ``provider_tax_code`` is the
    provider's default product tax code, for example ``txcd_10000000``. Pass any other field Core
    accepts (``email``, ``country``, ``default_currency``, ``billing_configuration``, ...) as a
    keyword. These calls send no ``Idempotency-Key``, so ``create()`` and ``update()`` are never
    retried; after a network error on ``create()``, ``get()`` the code before trying again.
    """

    @endpoint
    def list(self) -> Steps[BillingEntityPage]:
        operation = "billing_entities.list"
        require_secret_key(self._api_key, operation)
        payload = yield self._transport.call(operation, "GET", "/api/v1/billing_entities")
        return cast(BillingEntityPage, _object(operation, payload))

    @endpoint
    def get(self, code: str) -> Steps[BillingEntity]:
        operation = "billing_entities.get"
        require_secret_key(self._api_key, operation)
        require_non_empty(code, "code", operation)
        payload = yield self._transport.call(
            operation, "GET", f"/api/v1/billing_entities/{path_segment(code)}"
        )
        return cast(BillingEntity, _object(operation, payload)["billing_entity"])

    @endpoint
    def create(
        self,
        code: str,
        name: str,
        *,
        tax_provider: Optional[TaxProvider] = None,
        provider_tax_code: Optional[str] = None,
        **fields: Any,
    ) -> Steps[BillingEntity]:
        operation = "billing_entities.create"
        require_secret_key(self._api_key, operation)
        require_non_empty(code, "code", operation)
        require_non_empty(name, "name", operation)
        body = _body(operation, fields, FIELDS, tax_provider, provider_tax_code)
        body.update(code=code, name=name)
        payload = yield self._transport.call(
            operation, "POST", "/api/v1/billing_entities", json_body={"billing_entity": body}
        )
        return cast(BillingEntity, _object(operation, payload)["billing_entity"])

    @endpoint
    def update(
        self,
        code: str,
        *,
        name: Optional[str] = None,
        tax_provider: Optional[TaxProvider] = None,
        provider_tax_code: Optional[str] = None,
        **fields: Any,
    ) -> Steps[BillingEntity]:
        """Changes the given fields only. ``code`` cannot change. ``tax_codes`` and
        ``invoice_custom_section_codes`` replace the entity's taxes and invoice sections."""
        operation = "billing_entities.update"
        require_secret_key(self._api_key, operation)
        require_non_empty(code, "code", operation)
        body = _body(operation, fields, FIELDS + UPDATE_ONLY, tax_provider, provider_tax_code)
        if name is not None:
            body["name"] = name
        if not body:
            raise NozleValidationError(f"{operation} requires at least one field to change")
        payload = yield self._transport.call(
            operation,
            "PUT",
            f"/api/v1/billing_entities/{path_segment(code)}",
            json_body={"billing_entity": body},
        )
        return cast(BillingEntity, _object(operation, payload)["billing_entity"])


def _body(
    operation: str,
    fields: Mapping[str, Any],
    allowed: tuple[str, ...],
    tax_provider: Optional[str],
    provider_tax_code: Optional[str],
) -> Dict[str, Any]:
    unknown = sorted(set(fields) - set(allowed))
    if unknown:
        raise NozleValidationError(f"{operation} got unknown fields: {', '.join(unknown)}")
    if tax_provider is not None and tax_provider not in TAX_PROVIDERS:
        raise NozleValidationError(
            f"{operation} tax_provider must be one of {', '.join(TAX_PROVIDERS)}"
        )
    body = dict(fields)
    if tax_provider is not None:
        body["tax_provider"] = tax_provider
    if provider_tax_code is not None:
        body["provider_tax_code"] = provider_tax_code
    return body


def _object(operation: str, payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise NozleAPIError(operation, 200, "response was not a JSON object")
    return payload
