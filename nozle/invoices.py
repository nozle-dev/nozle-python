"""A customer's invoices, their PDFs and a preview of the next one."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, cast

from nozle._transport import HttpTransport
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.errors import NozleAPIError
from nozle.types import Invoice, InvoicePage


class InvoicesNamespace:
    """Reads a customer's invoices from Core."""

    def __init__(self, transport: HttpTransport, api_key: str) -> None:
        self._transport = transport
        self._api_key = api_key

    def list(
        self,
        customer_id: str,
        *,
        status: Optional[str] = None,
        payment_status: Optional[str] = None,
        issuing_date_from: Optional[str] = None,
        issuing_date_to: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
    ) -> InvoicePage:
        """A customer's invoices, newest first. Dates are inclusive ``YYYY-MM-DD``."""
        operation = "invoices.list"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        params: Dict[str, Any] = {"external_customer_id": customer_id}
        for name, value in (
            ("status", status),
            ("payment_status", payment_status),
            ("issuing_date_from", issuing_date_from),
            ("issuing_date_to", issuing_date_to),
            ("page", page),
            ("per_page", per_page),
        ):
            if value is not None:
                params[name] = value
        payload = self._transport.request(operation, "GET", "/api/v1/invoices", params=params)
        return cast(InvoicePage, _object(operation, payload))

    def get(self, invoice_id: str) -> Invoice:
        operation = "invoices.get"
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        payload = self._transport.request(
            operation, "GET", f"/api/v1/invoices/{path_segment(invoice_id)}"
        )
        return cast(Invoice, _object(operation, payload)["invoice"])

    def download_url(self, invoice_id: str) -> Optional[str]:
        """The PDF's URL, or ``None`` while Core is still generating it (ask again shortly)."""
        operation = "invoices.download_url"
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        payload = self._transport.request(
            operation,
            "POST",
            f"/api/v1/invoices/{path_segment(invoice_id)}/download",
            idempotent=True,
        )
        if not isinstance(payload, Mapping):
            return None
        invoice = payload.get("invoice")
        return invoice.get("file_url") if isinstance(invoice, Mapping) else None

    def preview(
        self,
        customer_id: str,
        *,
        subscription_ids: Optional[List[str]] = None,
        plan_code: Optional[str] = None,
        billing_time: Optional[str] = None,
    ) -> Invoice:
        """What the next invoice of ``subscription_ids`` would be, or the first invoice of a new
        subscription to ``plan_code``. Computed now and not saved."""
        operation = "invoices.preview"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        if not subscription_ids and not plan_code:
            raise ValueError("invoices.preview requires subscription_ids or plan_code")
        body: Dict[str, Any] = {"customer": {"external_id": customer_id}}
        if subscription_ids:
            body["subscriptions"] = {"external_ids": subscription_ids}
            if plan_code:
                body["subscriptions"]["plan_code"] = plan_code
        else:
            body["plan_code"] = plan_code
            if billing_time:
                body["billing_time"] = billing_time
        payload = self._transport.request(
            operation, "POST", "/api/v1/invoices/preview", json_body=body, idempotent=True
        )
        return cast(Invoice, _object(operation, payload)["invoice"])


def _object(operation: str, payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise NozleAPIError(operation, 200, "response was not a JSON object")
    return payload
