"""Verify webhooks Nozle sends to your endpoint."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence, Union

from nozle._operation import M, Resource, Steps, endpoint


@dataclass(frozen=True)
class NozleWebhookEvent:
    """A verified webhook. ``id`` is ``webhook-id``: use it to skip duplicates."""

    id: str
    type: str
    object_type: str
    organization_id: Optional[str]
    object: Any
    payload: dict = field(repr=False)


class WebhookVerificationError(Exception):
    """The request is not a genuine Nozle webhook. Answer it with 400 and do not act on it.

    ``code`` is ``missing_signature``, ``missing_webhook_secret``, ``invalid_signature`` or
    ``invalid_payload``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


Headers = Mapping[str, Any]


def _header(headers: Headers, name: str) -> Optional[str]:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            return None if value is None else str(value)
    return None


def _standard_matches(
    body: bytes, webhook_id: str, timestamp: str, signatures: str, secrets: Sequence[str]
) -> bool:
    """Standard Webhooks: any ``v1,`` entry may match any secret. Other versions are ignored."""
    signed = webhook_id.encode("utf-8") + b"." + timestamp.encode("utf-8") + b"." + body
    candidates = []
    for entry in signatures.split():
        version, _, value = entry.partition(",")
        if version != "v1":
            continue
        try:
            candidates.append(base64.b64decode(value, validate=True))
        except (ValueError, binascii.Error):
            continue
    matched = False
    for secret in secrets:
        try:
            key = base64.b64decode(secret.removeprefix("whsec_"))
        except (ValueError, binascii.Error):
            continue
        expected = hmac.new(key, signed, hashlib.sha256).digest()
        for received in candidates:  # no early exit: constant work per secret and signature
            matched |= hmac.compare_digest(received, expected)
    return matched


def _event(body: bytes, event_id: str) -> NozleWebhookEvent:
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError as error:
        raise WebhookVerificationError("invalid_payload", "webhook body is not JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("webhook_type"), str):
        raise WebhookVerificationError("invalid_payload", "webhook body has no webhook_type")
    raw_type = payload.get("object_type")
    object_type = raw_type if isinstance(raw_type, str) else ""
    organization_id = payload.get("organization_id")
    return NozleWebhookEvent(
        id=event_id,
        type=payload["webhook_type"],
        object_type=object_type,
        organization_id=organization_id if isinstance(organization_id, str) else None,
        object=payload.get(object_type) if object_type else None,
        payload=payload,
    )


class WebhooksNamespace(Resource[M]):
    """Verifies webhooks Nozle sends to your endpoint."""

    @endpoint
    def verify(
        self,
        body: Union[bytes, str],
        headers: Headers,
        *,
        webhook_secret: Union[str, Sequence[str]],
        tolerance_seconds: int = 300,
    ) -> Steps[NozleWebhookEvent]:
        """Checks ``webhook-signature`` against the raw body and returns the event.

        Pass the body exactly as received; parsed and re-serialised JSON fails the check.
        ``webhook_secret`` is the endpoint's ``whsec_`` secret; pass a list while one is being
        rotated. A ``webhook-timestamp`` more than ``tolerance_seconds`` from now fails. No request
        is made; on ``AsyncNozle`` the call is still awaited. Raises ``WebhookVerificationError``
        when the request is not genuine.
        """
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)
        webhook_id = _header(headers, "webhook-id")
        timestamp = _header(headers, "webhook-timestamp")
        signatures = _header(headers, "webhook-signature")
        if not webhook_id or not timestamp or not signatures:
            raise WebhookVerificationError(
                "missing_signature", "webhook-* signature headers are missing"
            )
        secrets = [webhook_secret] if isinstance(webhook_secret, str) else list(webhook_secret)
        secrets = [secret for secret in secrets if secret]
        if not secrets:
            raise WebhookVerificationError(
                "missing_webhook_secret", "webhooks need the endpoint's whsec_ secret"
            )
        try:
            age = abs(time.time() - int(timestamp))
        except ValueError:
            age = None
        if age is None or age > tolerance_seconds:
            raise WebhookVerificationError(
                "invalid_signature", "webhook timestamp is not acceptable"
            )
        if not _standard_matches(raw, webhook_id, timestamp, signatures, secrets):
            raise WebhookVerificationError("invalid_signature", "webhook signature does not match")
        return _event(raw, webhook_id)
        yield  # makes this an operation, so AsyncNozle awaits it like every other call
