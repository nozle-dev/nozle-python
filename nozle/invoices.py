"""A customer's invoices and their PDFs."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.errors import NozleAPIError
from nozle.types import Invoice, InvoicePage


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


def _object(operation: str, payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise NozleAPIError(operation, 200, "response was not a JSON object")
    return payload
