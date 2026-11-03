from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Type

#: The one code for a customer with no active subscription. Core's ``subscription_not_found``
#: counts as the same case in :meth:`NozleAPIError.has_code`.
NO_ACTIVE_SUBSCRIPTION = "active_subscription_not_found"


class TrialErrorCode(str, Enum):
    """Codes of a refused trial update: ``error_details["trial"]`` of a 422, except
    ``PLAN_CHANGE_PENDING`` (a 409 ``code``). Test with
    ``error.has_code(TrialErrorCode.NOT_ACTIVE)``; members equal their string value."""

    INVALID_PARAMS = "trial_invalid_params"
    NOT_ACTIVE = "trial_not_active"
    EXTENSION_TOO_LONG = "trial_extension_too_long"
    END_IN_PAST = "trial_end_in_past"
    PAYMENT_METHOD_REQUIRED = "payment_method_required"
    PLAN_CHANGE_PENDING = "plan_change_pending"


class EventVoidErrorCode(str, Enum):
    """Codes of a refused ``events.void``, and of a ``track`` of a voided ID
    (``TRANSACTION_VOIDED``, 409). Test with
    ``error.has_code(EventVoidErrorCode.PERIOD_INVOICED)``."""

    EVENT_NOT_FOUND = "event_not_found"
    PERIOD_INVOICED = "period_invoiced"
    REASON_REQUIRED = "reason_required"
    TRANSACTION_VOIDED = "transaction_voided"


class CustomerTaxErrorCode(str, Enum):
    """Codes for customer tax data. The first three arrive in ``error_details``
    (``tax_identification_type`` / ``tax_identification_number``) of a 422 ``customers.upsert``;
    ``TAX_LOCATION_REQUIRED`` (422, ``error_details["customer"]``: ``country_missing`` or
    ``address_missing``) and ``TAX_CHECK_PENDING`` (409, see ``retry_after_seconds``) refuse a
    checkout call. Test with ``error.has_code(CustomerTaxErrorCode.INVALID_FORMAT)``."""

    VALUE_IS_INVALID = "value_is_invalid"
    VALUE_IS_MANDATORY = "value_is_mandatory"
    INVALID_FORMAT = "invalid_format"
    TAX_LOCATION_REQUIRED = "customer_tax_location_required"
    TAX_CHECK_PENDING = "customer_tax_check_pending"
    # Invoice tax failures (``tax_status`` ``failed``).
    TAX_LOCATION_INVALID = "customer_tax_location_invalid"
    TAX_ID_INVALID = "tax_id_invalid"
    STRIPE_TAX_NOT_AUTHORIZED = "stripe_tax_not_authorized"
    STRIPE_ACCOUNT_REQUIRED = "stripe_account_required"
    TAX_PROVIDER_UNAVAILABLE = "tax_provider_unavailable"


class X402ErrorCode(str, Enum):
    """Codes of a refused ``invoices.get_x402_requirements`` or ``invoices.pay_with_x402``.
    ``FEATURE_NOT_ENABLED`` (403) and ``INVOICE_NOT_PAYABLE`` are the top-level ``code``;
    ``USD_REQUIRED`` (``error_details["currency"]``), ``STRIPE_REQUIRED``
    (``error_details["payment_provider"]``) and ``INVALID_FORMAT``
    (``error_details["transaction_hash"]``) arrive in a 422. Test with
    ``error.has_code(X402ErrorCode.USD_REQUIRED)``."""

    FEATURE_NOT_ENABLED = "feature_not_enabled"
    INVOICE_NOT_PAYABLE = "invoice_not_payable"
    USD_REQUIRED = "usd_required"
    STRIPE_REQUIRED = "stripe_required"
    INVALID_FORMAT = "invalid_format"


class RefusalReason(str, Enum):
    """Why ``can()``, ``usage.check()``, ``usage.track()`` or a hold answers ``allowed: False``
    (its ``reason``), and the ``code`` of the LLM proxy's 402/429 refusals.

    Members equal their string value, so compare
    ``result["reason"] == RefusalReason.BUDGET_EXCEEDED``. Results keep the plain string, so a
    reason a newer Engine adds still arrives.
    """

    FEATURE_NOT_FOUND = "feature_not_found"
    NO_SUBSCRIPTION = "no_subscription"
    SUBSCRIPTION_REQUIRED = "subscription_required"
    NO_ENTITLEMENT = "no_entitlement"
    USAGE_LIMIT_EXCEEDED = "usage_limit_exceeded"
    ECONOMIC_POLICY_DENIED = "economic_policy_denied"
    CUSTOMER_PAYMENT_OVERDUE = "customer_payment_overdue"
    INSUFFICIENT_CREDITS = "insufficient_credits"
    BUDGET_EXCEEDED = "budget_exceeded"


class NozleError(Exception):
    """Base exception for the Nozle SDK."""

    #: Set by ``track()`` to the transaction ID it sent (generated or passed). Pass it back
    #: as ``transaction_id`` when retrying, so the event is recorded once.
    transaction_id: Optional[str] = None


class NozleValidationError(NozleError, ValueError):
    """Raised before network I/O when a local SDK contract is invalid."""

    #: Position of the offending event, when ``track_batch()`` rejects one event's fields.
    index: Optional[int] = None


class NozleAuthenticationError(NozleValidationError):
    """Raised when an operation is attempted with the wrong key type."""


class NozleTransportError(NozleError):
    """Raised when an HTTP request could not reach Nozle."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        super().__init__(f"{operation} request failed before receiving an HTTP response")


class NozleAPIError(NozleError):
    """Structured, credential-safe error returned by a Nozle HTTP API."""

    def __init__(
        self,
        operation: str,
        status_code: int,
        response_details: Optional[Any] = None,
        request_id: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        self.operation = operation
        self.status_code = status_code
        self.response_details = response_details
        #: The ``X-Request-Id`` response header, when the service sent one. Quote it to support.
        self.request_id = request_id
        #: Seconds to wait before retrying, from the ``Retry-After`` header, when it was sent.
        self.retry_after = retry_after
        detail = ""
        if response_details not in (None, "", {}):
            detail = f": {response_details!r}"
        super().__init__(f"{operation} failed with HTTP {status_code}{detail}")

    @property
    def code(self) -> Optional[str]:
        """Top-level error code, for example ``payment_provider_required``."""
        details = self.response_details
        value = details.get("code") if isinstance(details, dict) else None
        return value if isinstance(value, str) else None

    @property
    def retry_after_seconds(self) -> Optional[float]:
        """Seconds to wait, from the body's ``retry_after_seconds`` (a 409
        ``customer_tax_check_pending``), when Core sent one."""
        details = self.response_details
        value = details.get("retry_after_seconds") if isinstance(details, dict) else None
        return (
            float(value)
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else None
        )

    def has_code(self, code: str) -> bool:
        """True when ``code`` is the top-level code or is listed in ``error_details``.

        A 422 ``validation_errors`` response lists specific codes, such as
        ``razorpay_requires_inr`` or ``invalid_checkout_signature``, under
        ``error_details``.
        """
        if self.code == code or (
            code == NO_ACTIVE_SUBSCRIPTION and self.code == "subscription_not_found"
        ):
            return True
        details = self.response_details
        listed = details.get("error_details") if isinstance(details, dict) else None
        if not isinstance(listed, dict):
            return False
        return any(
            value == code or (isinstance(value, list) and code in value)
            for value in listed.values()
        )


class BudgetExceededError(NozleAPIError):
    """HTTP 402 ``budget_exceeded``: a hard spend limit is used up."""

    @property
    def budget(self) -> Optional[Dict[str, Any]]:
        """The limit's standing (``limit``, ``spent``, ``held``, ``remaining``, ...), when sent."""
        details = self.response_details
        value = details.get("budget") if isinstance(details, dict) else None
        return value if isinstance(value, dict) else None


class UsageLimitExceededError(NozleAPIError):
    """HTTP 429 ``usage_limit_exceeded``: a hard unit limit is reached."""


def api_error_class(status_code: int, response_details: Any) -> Type[NozleAPIError]:
    """The error class for a failed response, by status and exact ``code``."""
    code = response_details.get("code") if isinstance(response_details, dict) else None
    if status_code == 402 and code == RefusalReason.BUDGET_EXCEEDED.value:
        return BudgetExceededError
    if status_code == 429 and code == RefusalReason.USAGE_LIMIT_EXCEEDED.value:
        return UsageLimitExceededError
    return NozleAPIError
