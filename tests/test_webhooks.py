from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest
import requests_mock

from nozle import Nozle, WebhookVerificationError
from nozle.webhooks import _standard_matches

V = json.loads((Path(__file__).parent / "fixtures" / "webhook_vectors.json").read_text())
BODY = V["body"]


def client() -> Nozle:
    return Nozle("sk_test", host="https://api.example")


S = V["standard"]
NOW = int(S["timestamp"])


def _sign(secret: str, webhook_id: str, timestamp: int, body: str) -> str:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{webhook_id}.{timestamp}.{body}".encode(), hashlib.sha256).digest()
    return "v1," + base64.b64encode(mac).decode()


def _standard(signature: str, timestamp: int = NOW) -> dict:
    return {
        "webhook-id": S["webhook_id"],
        "webhook-timestamp": str(timestamp),
        "webhook-signature": signature,
    }


@pytest.fixture
def at_vector_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nozle.webhooks.time.time", lambda: NOW)


def test_published_vector() -> None:
    assert _sign(S["secret"], S["webhook_id"], NOW, S["body"]) == S["signature"]
    assert _standard_matches(
        S["body"].encode(), S["webhook_id"], S["timestamp"], S["signature"], [S["secret"]]
    )


@pytest.mark.usefixtures("at_vector_time")
def test_returns_the_event_without_a_request(requests_mock: requests_mock.Mocker) -> None:
    signature = _sign(S["secret"], S["webhook_id"], NOW, BODY)
    event = client().webhooks.verify(BODY, _standard(signature), webhook_secret=S["secret"])
    assert event.id == S["webhook_id"]
    assert event.type == "subscription.started"
    assert requests_mock.call_count == 0


@pytest.mark.usefixtures("at_vector_time")
def test_accepts_rotation_and_ignores_other_versions() -> None:
    old, new = S["secret"], "whsec_" + base64.b64encode(b"x" * 24).decode()
    good = _sign(old, S["webhook_id"], NOW, BODY)
    other = _sign(new, S["webhook_id"], NOW, BODY)
    wrong = _sign("whsec_" + base64.b64encode(b"y" * 24).decode(), S["webhook_id"], NOW, BODY)
    verify = client().webhooks.verify
    assert verify(BODY, _standard(f"{wrong} {good}"), webhook_secret=old)
    assert verify(BODY, _standard(other), webhook_secret=[old, new])
    assert verify(BODY, _standard(f"v2,{wrong[3:]} {good}"), webhook_secret=old)
    with pytest.raises(WebhookVerificationError):
        verify(BODY, _standard("v2," + good[3:]), webhook_secret=old)


@pytest.mark.usefixtures("at_vector_time")
@pytest.mark.parametrize(
    ("offset", "ok"), [(299, True), (-299, True), (300, True), (301, False), (-301, False)]
)
def test_tolerance(offset: int, ok: bool) -> None:
    stamp = NOW - offset
    signature = _sign(S["secret"], S["webhook_id"], stamp, BODY)
    call = lambda: client().webhooks.verify(  # noqa: E731
        BODY, _standard(signature, stamp), webhook_secret=S["secret"]
    )
    if ok:
        assert call()
    else:
        with pytest.raises(WebhookVerificationError) as error:
            call()
        assert error.value.code == "invalid_timestamp"


@pytest.mark.usefixtures("at_vector_time")
def test_rejections() -> None:
    good = _sign(S["secret"], S["webhook_id"], NOW, BODY)
    other = "whsec_" + base64.b64encode(b"y" * 24).decode()
    cases = [
        (BODY, _standard(good), other, "invalid_signature"),
        (BODY.replace("pro", "max"), _standard(good), S["secret"], "invalid_signature"),
        (BODY, {**_standard(good), "webhook-timestamp": "abc"}, S["secret"], "invalid_timestamp"),
        (
            BODY,
            {k: v for k, v in _standard(good).items() if k != "webhook-id"},
            S["secret"],
            "missing_signature",
        ),
        (
            BODY,
            {k: v for k, v in _standard(good).items() if k != "webhook-signature"},
            S["secret"],
            "missing_signature",
        ),
        (BODY, _standard(good), [], "missing_secret"),
    ]
    for body, sent, secret, code in cases:
        with pytest.raises(WebhookVerificationError) as error:
            client().webhooks.verify(body, sent, webhook_secret=secret)
        assert error.value.code == code
