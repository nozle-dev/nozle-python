"""Customer tokens: short-lived, read-only tokens a browser sends with your publishable key."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import require_non_empty, require_secret_key
from nozle.types import CustomerToken, CustomerTokenRevocation


class CustomerTokensNamespace(Resource[M]):
    """Let your customer's browser read that one customer's entitlements, usage, credit balances
    and subscription with your publishable key. Needs a secret key and Core 11.59.0.

    A token reads only what both the minting key and the publishable key may read, and stops
    working when its customer is deleted. Core answers 403 ``publishable_key_not_allowed`` to a
    publishable key, 404 to an unknown customer and 422 to a ``ttl_seconds`` outside 60 to 3600 or
    an unknown feature code.
    """

    @endpoint
    def create(
        self,
        customer_id: str,
        ttl_seconds: Optional[int] = None,
        features: Optional[Sequence[str]] = None,
    ) -> Steps[CustomerToken]:
        """Mint a token for the signed-in customer. Take ``customer_id`` (the customer's external
        id) from your own session, never from the browser. The browser sends ``token`` as
        ``X-Nozle-Customer-Token`` with your publishable key. It is a bearer credential: never log
        it.

        ``ttl_seconds`` is 60 to 3600 (one hour, also the default). ``features`` are feature codes:
        ``can`` and usage reads are limited to them; subscription and credit reads are not.
        Retried like a read, because a repeat only mints another token.
        """
        operation = "customer_tokens.create"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        body: Dict[str, Any] = {"customer_id": customer_id}
        if ttl_seconds is not None:
            body["ttl_seconds"] = ttl_seconds
        if features is not None:
            body["features"] = list(features)
        payload = yield self._transport.call(
            operation, "POST", "/api/v1/customer_tokens", json_body=body, idempotent=True
        )
        return cast(CustomerToken, payload)

    @endpoint
    def revoke(self) -> Steps[CustomerTokenRevocation]:
        """Refuse every customer token minted until now, for example after one leaked. Tokens
        minted afterwards work. Needs a secret key with ``customer:write``."""
        operation = "customer_tokens.revoke"
        require_secret_key(self._api_key, operation)
        payload = yield self._transport.call(
            operation, "POST", "/api/v1/customer_tokens/revoke", json_body={}, idempotent=True
        )
        return cast(CustomerTokenRevocation, payload)
