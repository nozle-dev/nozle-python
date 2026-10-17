from __future__ import annotations

from typing import Any, Optional


class NozleError(Exception):
    """Base exception for the Nozle SDK."""


class NozleValidationError(NozleError, ValueError):
    """Raised before network I/O when a local SDK contract is invalid."""


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
        if self.code == code:
            return True
        details = self.response_details
        listed = details.get("error_details") if isinstance(details, dict) else None
        if not isinstance(listed, dict):
            return False
        return any(
            value == code or (isinstance(value, list) and code in value)
            for value in listed.values()
        )
