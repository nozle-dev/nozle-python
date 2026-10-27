"""A suspended workspace answers every call with 402 workspace_suspended: raised, never retried."""

from __future__ import annotations

from typing import Any, Callable, List

import httpx
import pytest
import requests_mock
from requests_mock import ANY

from nozle import AsyncNozle, Nozle, NozleAPIError

BASE = "https://api.example/engine"
BODY = {
    "code": "workspace_suspended",
    "error": "This workspace is suspended because its Nozle invoices are unpaid.",
}

CALLS: List[Callable[[Any], Any]] = [
    lambda n: n.can("c", "f"),
    lambda n: n.track("c", "e", subscription_id="s"),
    lambda n: n.usage.check("c", "f"),
    lambda n: n.plans(),
]


def _assert_suspended(error: NozleAPIError) -> None:
    assert error.status_code == 402
    assert error.code == "workspace_suspended"


@pytest.mark.parametrize("call", CALLS)
def test_402_raises_with_code_and_is_not_retried(
    call: Callable[[Any], Any],
    requests_mock: requests_mock.Mocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("nozle._transport.time.sleep", lambda s: None)
    matcher = requests_mock.register_uri(ANY, ANY, status_code=402, json=BODY)
    with pytest.raises(NozleAPIError) as raised:
        call(Nozle("sk_test", base_url=BASE, max_retries=3))
    _assert_suspended(raised.value)
    assert matcher.call_count == 1


@pytest.mark.parametrize("call", CALLS)
async def test_async_402_raises_with_code_and_is_not_retried(call: Callable[[Any], Any]) -> None:
    seen: List[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(402, json=BODY)

    nozle = AsyncNozle(
        "sk_test",
        base_url=BASE,
        max_retries=3,
        _client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(NozleAPIError) as raised:
        await call(nozle)
    _assert_suspended(raised.value)
    assert len(seen) == 1
