"""Verify webhooks Nozle sends to your endpoint."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import threading
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Tuple, Union

from nozle._transport import HttpTransport
from nozle._validation import require_secret_key


@dataclass(frozen=True)
class NozleWebhookEvent:
    """A verified webhook. ``id`` is ``X-Nozle-Unique-Key``: use it to skip duplicates."""

    id: str
    type: str
    object_type: str
    organization_id: Optional[str]
    object: Any
    payload: dict = field(repr=False)


class WebhookVerificationError(Exception):
    """The request is not a genuine Nozle webhook. Answer it with 400 and do not act on it.

    ``code`` is ``missing_signature``, ``unsupported_algorithm``, ``missing_hmac_key``,
    ``invalid_signature`` or ``invalid_payload``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


Headers = Mapping[str, Any]

# DER prefix of the SHA-256 DigestInfo in an RSASSA-PKCS1-v1_5 signature (RFC 8017, section 9.2).
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _header(headers: Headers, name: str) -> Optional[str]:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            return None if value is None else str(value)
    return None


def _b64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _der(data: bytes, offset: int) -> Tuple[int, bytes, int]:
    """Reads one DER element at ``offset``: its tag, contents and the offset after it."""
    tag = data[offset]
    length = data[offset + 1]
    offset += 2
    if length & 0x80:
        count = length & 0x7F
        length = int.from_bytes(data[offset : offset + count], "big")
        offset += count
    return tag, data[offset : offset + length], offset + length


def _rsa_public_key(pem: str) -> Tuple[int, int]:
    """(n, e) from a PEM ``PUBLIC KEY`` (SubjectPublicKeyInfo) or ``RSA PUBLIC KEY`` (PKCS#1)."""
    lines = [line.strip() for line in pem.strip().splitlines()]
    der = base64.b64decode("".join(line for line in lines if line and not line.startswith("-----")))
    _, outer, _ = _der(der, 0)
    if lines[0] == "-----BEGIN PUBLIC KEY-----":
        _, _, after_algorithm = _der(outer, 0)
        _, bit_string, _ = _der(outer, after_algorithm)
        _, outer, _ = _der(bit_string[1:], 0)
    _, n, after_n = _der(outer, 0)
    _, e, _ = _der(outer, after_n)
    return int.from_bytes(n, "big"), int.from_bytes(e, "big")


def _rs256_valid(signed: bytes, signature: bytes, key: Tuple[int, int]) -> bool:
    # Rebuild the one valid PKCS#1 v1.5 encoding and compare it whole, instead of parsing the
    # decrypted signature.
    n, e = key
    size = (n.bit_length() + 7) // 8
    if len(signature) != size:
        return False
    value = int.from_bytes(signature, "big")
    if value >= n:
        return False
    encoded = pow(value, e, n).to_bytes(size, "big")
    digest = _SHA256_DIGEST_INFO + hashlib.sha256(signed).digest()
    expected = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    return hmac.compare_digest(encoded, expected)


def _jwt_matches(body: bytes, token: str, key: Tuple[int, int]) -> bool:
    parts = token.split(".")
    if len(parts) != 3:
        return False
    try:
        head = json.loads(_b64url(parts[0]))
        if head.get("alg") != "RS256":
            return False
        if not _rs256_valid(f"{parts[0]}.{parts[1]}".encode(), _b64url(parts[2]), key):
            return False
        data = json.loads(_b64url(parts[1])).get("data")
    except (ValueError, binascii.Error, AttributeError):
        return False
    return isinstance(data, str) and hmac.compare_digest(data.encode("utf-8"), body)


def _hmac_matches(body: bytes, signature: str, key: str) -> bool:
    expected = hmac.new(key.encode("utf-8"), body, hashlib.sha256).digest()
    try:
        received = base64.b64decode(signature, validate=True)
    except (ValueError, binascii.Error):
        return False
    return hmac.compare_digest(received, expected)


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


class WebhooksNamespace:
    """Verifies webhooks Nozle sends to your endpoint."""

    def __init__(self, transport: HttpTransport, api_key: str) -> None:
        self._transport = transport
        self._api_key = api_key
        self._public_key: Optional[Tuple[int, int]] = None
        self._lock = threading.Lock()

    def verify(
        self,
        body: Union[bytes, str],
        headers: Headers,
        *,
        hmac_key: Optional[str] = None,
    ) -> NozleWebhookEvent:
        """Checks ``X-Nozle-Signature`` against the raw body and returns the event.

        Pass the body exactly as received; parsed and re-serialised JSON fails the check. ``hmac``
        endpoints need the organization's ``hmac_key``. ``jwt`` endpoints need nothing: the public
        key is read once from Core with your secret key and cached. Raises
        ``WebhookVerificationError`` when the request is not genuine.
        """
        signature = _header(headers, "x-nozle-signature")
        algorithm = _header(headers, "x-nozle-signature-algorithm")
        event_id = _header(headers, "x-nozle-unique-key") or ""
        if not signature or not algorithm:
            raise WebhookVerificationError(
                "missing_signature", "X-Nozle-Signature headers are missing"
            )
        raw = body.encode("utf-8") if isinstance(body, str) else bytes(body)

        if algorithm == "hmac":
            if not hmac_key:
                raise WebhookVerificationError(
                    "missing_hmac_key", "hmac webhooks need the organization's hmac_key"
                )
            if not _hmac_matches(raw, signature, hmac_key):
                raise WebhookVerificationError(
                    "invalid_signature", "webhook signature does not match"
                )
        elif algorithm == "jwt":
            valid = _jwt_matches(raw, signature, self._key())
            if not valid:
                # The key may have been rotated since it was cached: read it again once.
                valid = _jwt_matches(raw, signature, self._key(refresh=True))
            if not valid:
                raise WebhookVerificationError(
                    "invalid_signature", "webhook signature does not match"
                )
        else:
            raise WebhookVerificationError(
                "unsupported_algorithm", f"unsupported signature algorithm {algorithm}"
            )
        return _event(raw, event_id)

    def _key(self, refresh: bool = False) -> Tuple[int, int]:
        with self._lock:
            if refresh or self._public_key is None:
                require_secret_key(self._api_key, "webhooks.verify")
                response = self._transport.request(
                    "webhooks.public_key", "GET", "/api/v1/webhooks/json_public_key"
                )
                encoded = ((response or {}).get("webhook") or {}).get("public_key") or ""
                self._public_key = _rsa_public_key(base64.b64decode(encoded).decode("utf-8"))
            return self._public_key
