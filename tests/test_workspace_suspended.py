"""402 workspace_suspended and card_required are raised with their code, never retried."""

from __future__ import annotations

from typing import Any, Callable, List

import httpx
import pytest
import requests_mock
from requests_mock import ANY

from nozle import AsyncNozle, Nozle, NozleAPIError

BASE = "https://api.example/engine"
BODIES = {
    "workspace_suspended": {
        "code": "workspace_suspended",
        "error": "This workspace is suspended because its Nozle invoices are unpaid.",
    },
    "card_required": {
        "code": "card_required",
        "error": "Add a card to your Nozle workspace to use the API.",
    },
}

CALLS: List[Callable[[Any], Any]] = [
    lambda n: n.can("c", "f"),
    lambda n: n.track("c", "e", subscription_id="s"),
    lambda n: n.usage.check("c", "f"),
    lambda n: n.plans(),
]


def _assert_402(error: NozleAPIError, code: str) -> None:
    assert error.status_code == 402
    assert error.code == code


@pytest.mark.parametrize("code", list(BODIES))
@pytest.mark.parametrize("call", CALLS)
def test_402_raises_with_code_and_is_not_retried(
    call: Callable[[Any], Any],
    code: str,
    requests_mock: requests_mock.Mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nozle._transport.time.sleep", lambda s: None)
    matcher = requests_mock.register_uri(ANY, ANY, status_code=402, json=BODIES[code])
    with pytest.raises(NozleAPIError) as raised:
        call(Nozle("sk_test", base_url=BASE, max_retries=3))
    _assert_402(raised.value, code)
    assert matcher.call_count == 1


@pytest.mark.parametrize("code", list(BODIES))
@pytest.mark.parametrize("call", CALLS)
async def test_async_402_raises_with_code_and_is_not_retried(
    call: Callable[[Any], Any], code: str
) -> None:
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(402, json=BODIES[code])

    nozle = AsyncNozle(
        "sk_test",
        base_url=BASE,
        max_retries=3,
        _client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(NozleAPIError) as raised:
        await call(nozle)
    _assert_402(raised.value, code)
    assert len(seen) == 1


def test_package_has_no_embedded_checkout_secret() -> None:
    """Stripe checkout is hosted only: no client_secret in the SDK source or README."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    files = [*(root / "nozle").rglob("*.py"), root / "README.md"]
    for file in files:
        text = file.read_text()
        assert "client_secret" not in text and "clientSecret" not in text, file
