from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
import requests_mock

from nozle import Nozle, WebhookVerificationError
from nozle.webhooks import _rsa_public_key

# Signed with throwaway RSA keys; the private keys are not kept.
V = json.loads((Path(__file__).parent / "fixtures" / "webhook_vectors.json").read_text())
BODY = V["body"]
KEY_URL = "https://api.example/core/api/v1/webhooks/json_public_key"


def headers(signature: str, algorithm: str) -> dict:
    return {
        "X-Nozle-Signature": signature,
        "X-Nozle-Signature-Algorithm": algorithm,
        "X-Nozle-Unique-Key": "wh_1",
    }


def client() -> Nozle:
    return Nozle("sk_test", host="https://api.example")


def test_hmac_signature_returns_the_event() -> None:
    event = client().webhooks.verify(
        BODY.encode(), headers(V["hmac_signature"], "hmac"), hmac_key=V["hmac_key"]
    )
    assert event.id == "wh_1"
    assert event.type == "subscription.started"
    assert event.object_type == "subscription"
    assert event.organization_id == "org_1"
    assert event.object == {"external_id": "sub_1", "plan_code": "pro"}


@pytest.mark.parametrize(
    ("body", "key", "code"),
    [
        (BODY.replace("pro", "max"), "org-hmac-key", "invalid_signature"),
        (BODY, "other-key", "invalid_signature"),
        (BODY, None, "missing_hmac_key"),
    ],
)
def test_hmac_rejections(body: str, key: str | None, code: str) -> None:
    with pytest.raises(WebhookVerificationError) as error:
        client().webhooks.verify(body, headers(V["hmac_signature"], "hmac"), hmac_key=key)
    assert error.value.code == code


def test_missing_headers_and_unknown_algorithm() -> None:
    with pytest.raises(WebhookVerificationError) as error:
        client().webhooks.verify(BODY, {})
    assert error.value.code == "missing_signature"
    with pytest.raises(WebhookVerificationError) as error:
        client().webhooks.verify(BODY, headers("x", "md5"))
    assert error.value.code == "unsupported_algorithm"


def test_jwt_reads_the_key_once_with_the_secret_key(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(KEY_URL, json={"webhook": {"public_key": V["public_key_spki_b64"]}})
    nozle = client()
    nozle.webhooks.verify(BODY, headers(V["jwt_valid"], "jwt"))
    nozle.webhooks.verify(
        BODY.encode(), {k.lower(): v for k, v in headers(V["jwt_valid"], "jwt").items()}
    )
    assert route.call_count == 1
    assert route.last_request.headers["Authorization"] == "Bearer sk_test"


@pytest.mark.parametrize("token", ["jwt_other_body", "jwt_other_key", "jwt_hs256_header"])
def test_jwt_rejections(requests_mock: requests_mock.Mocker, token: str) -> None:
    requests_mock.get(KEY_URL, json={"webhook": {"public_key": V["public_key_spki_b64"]}})
    with pytest.raises(WebhookVerificationError) as error:
        client().webhooks.verify(BODY, headers(V[token], "jwt"))
    assert error.value.code == "invalid_signature"


def test_jwt_reads_the_key_again_after_a_rotation(requests_mock: requests_mock.Mocker) -> None:
    route = requests_mock.get(
        KEY_URL,
        [
            {"json": {"webhook": {"public_key": V["rotated_public_key_spki_b64"]}}},
            {"json": {"webhook": {"public_key": V["public_key_spki_b64"]}}},
        ],
    )
    client().webhooks.verify(BODY, headers(V["jwt_valid"], "jwt"))
    assert route.call_count == 2


def test_pkcs1_and_spki_keys_parse_to_the_same_numbers() -> None:
    spki = base64.b64decode(V["public_key_spki_b64"]).decode()
    pkcs1 = base64.b64decode(V["public_key_pkcs1_b64"]).decode()
    n, e = _rsa_public_key(spki)
    assert (n, e) == _rsa_public_key(pkcs1)
    assert e == 65537 and n.bit_length() == 2048
