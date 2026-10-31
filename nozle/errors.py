from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional, Type

#: The one code for a customer with no active subscription. Core's ``subscription_not_found``
#: counts as the same case in :meth:`NozleAPIError.has_code`.
NO_ACTIVE_SUBSCRIPTION = "active_subscription_not_found"


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
