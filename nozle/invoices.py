"""A customer's invoices and their PDFs."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.errors import NozleAPIError
from nozle.types import Invoice, InvoicePage, X402Requirements


class InvoicesNamespace(Resource[M]):
    """Reads a customer's invoices from Core."""

    @endpoint
    def list(
        self,
        customer_id: str,
        *,
        status: Optional[str] = None,
        payment_status: Optional[str] = None,
        payment_overdue: Optional[bool] = None,
        issuing_date_from: Optional[str] = None,
        issuing_date_to: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
    ) -> Steps[InvoicePage]:
        """A customer's invoices, newest first. Dates are inclusive ``YYYY-MM-DD``.
        ``payment_overdue=True`` keeps only unpaid invoices past their due date."""
        operation = "invoices.list"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        params: Dict[str, Any] = {"external_customer_id": customer_id}
        for name, value in (
            ("status", status),
            ("payment_status", payment_status),
            ("payment_overdue", None if payment_overdue is None else str(payment_overdue).lower()),
            ("issuing_date_from", issuing_date_from),
            ("issuing_date_to", issuing_date_to),
            ("page", page),
            ("per_page", per_page),
        ):
            if value is not None:
                params[name] = value
        payload = yield self._transport.call(operation, "GET", "/api/v1/invoices", params=params)
        return cast(InvoicePage, _object(operation, payload))

    @endpoint
    def get(self, invoice_id: str) -> Steps[Invoice]:
        operation = "invoices.get"
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        payload = yield self._transport.call(
            operation, "GET", f"/api/v1/invoices/{path_segment(invoice_id)}"
        )
        return cast(Invoice, _object(operation, payload)["invoice"])

    @endpoint
    def download_url(self, invoice_id: str) -> Steps[Optional[str]]:
        """The PDF's URL, or ``None`` while Core is still generating it (ask again shortly)."""
        operation = "invoices.download_url"
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        payload = yield self._transport.call(
            operation,
            "POST",
            f"/api/v1/invoices/{path_segment(invoice_id)}/download",
            idempotent=True,
        )
        if not isinstance(payload, Mapping):
            return None
        invoice = payload.get("invoice")
        return invoice.get("file_url") if isinstance(invoice, Mapping) else None

    @endpoint
    def pay_with_agent_token(self, invoice_id: str, agent_payment_token: str) -> Steps[Invoice]:
        """Pay a finalized invoice with a Stripe Shared Payment Token (``spt_...``) an AI agent
        handed you. Needs the ``stripe_agent_payment_token`` workspace flag and a Stripe connection;
        returns the invoice. A refused token raises ``NozleAPIError`` (422)
        with the code in ``error_details`` (``token_expired``, ``currency_mismatch``,
        ``amount_exceeds_limit``, ``token_deactivated``, ``invalid_format``). Never retried."""
        operation = "invoices.pay_with_agent_token"
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        require_non_empty(agent_payment_token, "agent_payment_token", operation)
        payload = yield self._transport.call(
            operation,
            "POST",
            f"/api/v1/invoices/{path_segment(invoice_id)}/pay_with_agent_token",
            json_body={"agent_payment_token": agent_payment_token},
        )
        return cast(Invoice, _object(operation, payload)["invoice"])

    @endpoint
    def get_x402_requirements(self, invoice_id: str) -> Steps[X402Requirements]:
        """The x402 (v2) payment requirements for a finalized USD invoice: the USDC amount and the
        Stripe deposit address (``payTo``) to relay to a paying agent in an HTTP 402 response.
        Needs the ``stripe_x402_payments`` workspace flag and a Stripe connection. A refusal raises
        ``NozleAPIError``; test with ``has_code(X402ErrorCode.FEATURE_NOT_ENABLED)``,
        ``INVOICE_NOT_PAYABLE``, ``USD_REQUIRED`` or ``STRIPE_REQUIRED``."""
        operation = "invoices.get_x402_requirements"
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        payload = yield self._transport.call(
            operation, "GET", f"/api/v1/invoices/{path_segment(invoice_id)}/x402_requirements"
        )
        return cast(X402Requirements, _object(operation, payload)["x402_requirements"])

    @endpoint
    def pay_with_x402(self, invoice_id: str, transaction_hash: str) -> Steps[Invoice]:
        """Pay a finalized USD invoice with the hash of an on-chain USDC transfer the agent sent
        to the ``payTo`` address of :meth:`get_x402_requirements`. Returns the invoice; the
        payment is often still ``processing`` and finishes through Stripe's webhook. One
        transaction pays one invoice once. Refusals as in :meth:`get_x402_requirements`, plus
        ``INVALID_FORMAT`` for a malformed hash. Never retried."""
        operation = "invoices.pay_with_x402"
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        require_non_empty(transaction_hash, "transaction_hash", operation)
        payload = yield self._transport.call(
            operation,
            "POST",
            f"/api/v1/invoices/{path_segment(invoice_id)}/pay_with_x402",
            json_body={"transaction_hash": transaction_hash},
        )
        return cast(Invoice, _object(operation, payload)["invoice"])


def _object(operation: str, payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise NozleAPIError(operation, 200, "response was not a JSON object")
    return payload
