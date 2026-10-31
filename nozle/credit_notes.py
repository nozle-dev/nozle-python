"""Credit notes: preview, issue, refund, list and void, through Core."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.errors import NozleAPIError, NozleValidationError
from nozle.types import (
    CreditNote,
    CreditNoteItem,
    CreditNotePage,
    CreditNotePreview,
    CreditNoteReason,
)

REASONS = (
    "duplicated_charge",
    "product_unsatisfactory",
    "order_change",
    "order_cancellation",
    "fraudulent_charge",
    "other",
)

#: Codes Core puts in ``NozleAPIError.code`` or ``error_details``; test them with
#: ``error.has_code(...)``. ``refund()`` raises ``refund_exceeds_paid_amount``; Core the rest.
INVALID_TYPE_OR_STATUS = "invalid_type_or_status"
CANNOT_REFUND_UNPAID_INVOICE = "cannot_refund_unpaid_invoice"
HIGHER_THAN_REMAINING_INVOICE_AMOUNT = "higher_than_remaining_invoice_amount"
HIGHER_THAN_REMAINING_FEE_AMOUNT = "higher_than_remaining_fee_amount"
DOES_NOT_MATCH_ITEM_AMOUNTS = "does_not_match_item_amounts"
TOTAL_AMOUNT_MUST_BE_POSITIVE = "total_amount_must_be_positive"
NO_VOIDABLE_AMOUNT = "no_voidable_amount"
REFUND_EXCEEDS_PAID_AMOUNT = "refund_exceeds_paid_amount"


class CreditNotesNamespace(Resource[M]):
    """Credit notes lower a finalized invoice. Their total is credited to the customer's balance,
    refunded to the original payment method (Stripe or Razorpay, started by Nozle), offset against
    what the customer still owes, or split between the three.

    These calls send no ``Idempotency-Key`` yet, so ``create()``,
    ``refund()`` and ``void()`` are never retried, whatever ``max_retries`` says; ``preview()``,
    ``get()`` and ``list()`` follow it. After a network error or a 5xx on ``create()``, call
    ``list(customer_id=..., invoice_number=...)`` before trying again.
    """

    @endpoint
    def preview(self, invoice_id: str, items: Sequence[CreditNoteItem]) -> Steps[CreditNotePreview]:
        """What a credit note for these items would be worth, with taxes and coupons applied.
        Creates nothing. ``max_refundable_amount_cents`` is what was paid and not refunded yet."""
        return (yield from self._preview(invoice_id, items))

    @endpoint
    def create(
        self,
        invoice_id: str,
        items: Sequence[CreditNoteItem],
        *,
        credit_amount_cents: Optional[int] = None,
        refund_amount_cents: Optional[int] = None,
        offset_amount_cents: Optional[int] = None,
        reason: Optional[CreditNoteReason] = None,
        description: Optional[str] = None,
        metadata: Optional[Mapping[str, Optional[str]]] = None,
    ) -> Steps[CreditNote]:
        """Issues a credit note. The three amounts must add up to the total of ``items`` with
        taxes (take it from ``preview()``) and at least one must be above zero.

        ``credit_amount_cents`` goes to the customer's balance, ``refund_amount_cents`` back to the
        original payment method (only what was paid and not refunded yet; follow ``refund_status``
        with ``get()``), ``offset_amount_cents`` off what the customer still owes on the invoice.
        """
        operation = "credit_notes.create"
        return (
            yield from self._create(
                operation,
                invoice_id,
                items,
                credit_amount_cents,
                refund_amount_cents,
                offset_amount_cents,
                reason,
                description,
                metadata,
            )
        )

    @endpoint
    def refund(
        self,
        invoice_id: str,
        items: Sequence[CreditNoteItem],
        *,
        reason: Optional[CreditNoteReason] = None,
        description: Optional[str] = None,
        metadata: Optional[Mapping[str, Optional[str]]] = None,
        credit_remainder: bool = False,
    ) -> Steps[CreditNote]:
        """Refunds the items to the original payment method: ``preview()`` for the exact total,
        then ``create()`` with that total as the refund. Two calls; the second is not retried.

        When less was paid than the items are worth this raises ``NozleAPIError`` with code
        ``refund_exceeds_paid_amount`` and creates nothing, unless ``credit_remainder=True``: then
        what was paid is refunded and the rest credited to the customer's balance.
        """
        operation = "credit_notes.refund"
        preview = yield from self._preview(invoice_id, items, operation)
        total = preview["max_creditable_amount_cents"]
        refundable = min(preview["max_refundable_amount_cents"], total)
        remainder = total - refundable
        if refundable <= 0 or (remainder > 0 and not credit_remainder):
            hint = "; pass credit_remainder=True to credit the rest" if refundable > 0 else ""
            raise NozleAPIError(
                operation,
                422,
                {
                    "code": REFUND_EXCEEDS_PAID_AMOUNT,
                    "error": f"{refundable} of {total} cents can be refunded{hint}",
                },
            )
        return (
            yield from self._create(
                operation,
                invoice_id,
                items,
                remainder if remainder > 0 else None,
                refundable,
                None,
                reason,
                description,
                metadata,
            )
        )

    @endpoint
    def list(
        self,
        *,
        customer_id: Optional[str] = None,
        invoice_number: Optional[str] = None,
        reason: Optional[CreditNoteReason] = None,
        credit_status: Optional[str] = None,
        refund_status: Optional[str] = None,
        currency: Optional[str] = None,
        issuing_date_from: Optional[str] = None,
        issuing_date_to: Optional[str] = None,
        amount_from: Optional[int] = None,
        amount_to: Optional[int] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
    ) -> Steps[CreditNotePage]:
        """Credit notes, newest first: all of them, or one customer's. ``credit_status`` is
        ``available``, ``consumed`` or ``voided``; ``refund_status`` is ``pending``, ``succeeded``
        or ``failed``; dates are inclusive ``YYYY-MM-DD``."""
        operation = "credit_notes.list"
        require_secret_key(self._api_key, operation)
        params: Dict[str, Any] = {}
        for name, value in (
            ("external_customer_id", customer_id),
            ("invoice_number", invoice_number),
            ("reason", reason),
            ("credit_status", credit_status),
            ("refund_status", refund_status),
            ("currency", currency),
            ("issuing_date_from", issuing_date_from),
            ("issuing_date_to", issuing_date_to),
            ("amount_from", amount_from),
            ("amount_to", amount_to),
            ("page", page),
            ("per_page", per_page),
        ):
            if value is not None:
                params[name] = value
        payload = yield self._transport.call(
            operation, "GET", "/api/v1/credit_notes", params=params or None
        )
        return cast(CreditNotePage, _object(operation, payload))

    @endpoint
    def get(self, credit_note_id: str) -> Steps[CreditNote]:
        operation = "credit_notes.get"
        require_secret_key(self._api_key, operation)
        require_non_empty(credit_note_id, "credit_note_id", operation)
        payload = yield self._transport.call(
            operation, "GET", f"/api/v1/credit_notes/{path_segment(credit_note_id)}"
        )
        return cast(CreditNote, _object(operation, payload)["credit_note"])

    @endpoint
    def void(self, credit_note_id: str) -> Steps[CreditNote]:
        """Voids what is left of the credit note's credit balance. It does not undo a refund that
        was paid out or an offset already applied. Raises code ``no_voidable_amount`` (422) when
        nothing is left. Never retried."""
        operation = "credit_notes.void"
        require_secret_key(self._api_key, operation)
        require_non_empty(credit_note_id, "credit_note_id", operation)
        payload = yield self._transport.call(
            operation, "PUT", f"/api/v1/credit_notes/{path_segment(credit_note_id)}/void"
        )
        return cast(CreditNote, _object(operation, payload)["credit_note"])

    def _preview(
        self,
        invoice_id: str,
        items: Sequence[CreditNoteItem],
        operation: str = "credit_notes.preview",
    ) -> Steps[CreditNotePreview]:
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        body = {"credit_note": {"invoice_id": invoice_id, "items": _items(items, operation)}}
        payload = yield self._transport.call(
            operation,
            "POST",
            "/api/v1/credit_notes/estimate",
            json_body=body,
            idempotent=True,  # creates nothing
        )
        return cast(CreditNotePreview, _object(operation, payload)["estimated_credit_note"])

    def _create(
        self,
        operation: str,
        invoice_id: str,
        items: Sequence[CreditNoteItem],
        credit_amount_cents: Optional[int],
        refund_amount_cents: Optional[int],
        offset_amount_cents: Optional[int],
        reason: Optional[str],
        description: Optional[str],
        metadata: Optional[Mapping[str, Optional[str]]],
    ) -> Steps[CreditNote]:
        require_secret_key(self._api_key, operation)
        require_non_empty(invoice_id, "invoice_id", operation)
        credit_note: Dict[str, Any] = {"invoice_id": invoice_id, "items": _items(items, operation)}
        amounts = (
            ("credit_amount_cents", credit_amount_cents),
            ("refund_amount_cents", refund_amount_cents),
            ("offset_amount_cents", offset_amount_cents),
        )
        for name, value in amounts:
            if value is not None and (type(value) is not int or value < 0):
                raise NozleValidationError(f"{operation} {name} must be whole cents, 0 or more")
        if sum(value or 0 for _, value in amounts) <= 0:
            raise NozleValidationError(
                f"{operation} requires credit_amount_cents, refund_amount_cents or "
                "offset_amount_cents above zero"
            )
        if reason is not None and reason not in REASONS:
            raise NozleValidationError(f"{operation} reason must be one of {', '.join(REASONS)}")
        optional: Dict[str, Any] = dict(amounts)
        optional.update(
            reason=reason,
            description=description,
            metadata=None if metadata is None else dict(metadata),
        )
        credit_note.update({name: value for name, value in optional.items() if value is not None})
        payload = yield self._transport.call(
            operation, "POST", "/api/v1/credit_notes", json_body={"credit_note": credit_note}
        )
        return cast(CreditNote, _object(operation, payload)["credit_note"])


def _items(items: Sequence[CreditNoteItem], operation: str) -> list[Dict[str, Any]]:
    if not isinstance(items, (list, tuple)) or not items:
        raise NozleValidationError(f"{operation} requires at least one item")
    checked = []
    for item in items:
        fee_id = item.get("fee_id") if isinstance(item, Mapping) else None
        amount = item.get("amount_cents") if isinstance(item, Mapping) else None
        require_non_empty(cast(str, fee_id), "items[].fee_id", operation)
        if type(amount) is not int or amount < 0:
            raise NozleValidationError(
                f"{operation} items[].amount_cents must be whole cents, 0 or more"
            )
        checked.append({"fee_id": fee_id, "amount_cents": amount})
    return checked


def _object(operation: str, payload: Any) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise NozleAPIError(operation, 200, "response was not a JSON object")
    return payload
