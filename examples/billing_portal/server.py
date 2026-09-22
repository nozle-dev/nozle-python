"""Authenticated, single-process merchant example for React cancellation controls."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit

import requests

from nozle import Nozle
from nozle.errors import NozleAPIError


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ActionStore:
    """Persist replay bindings. Use a transactional shared store for multiple workers."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.entries = json.loads(path.read_text()) if path.exists() else {}
        self.lock = threading.Lock()

    def save(self, key: str, entry: dict[str, Any]) -> None:
        self.entries[key] = entry
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as output:
            json.dump(self.entries, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)


def fields(body: Any, allowed: set[str]) -> None:
    if not isinstance(body, dict) or body.keys() - allowed:
        raise HttpError(400, "Invalid request fields.")


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > 255:
        raise HttpError(400, "Invalid identifier.")
    return value


def date(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT.+(?:Z|[+-]\d\d:\d\d)", value
    ):
        raise HttpError(400, "Invalid effective date.")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value  # Preserve backend microseconds for the atomic confirmation-date guard.
    except ValueError:
        raise HttpError(400, "Invalid effective date.") from None


def transition(customer_id: str, body: dict[str, Any]) -> dict[str, Any]:
    operation = body.get("operation")
    if operation not in ("cancel", "uncancel"):
        raise HttpError(400, "Unsupported operation.")
    return {
        "customer_id": customer_id,
        "subscription_id": identifier(body.get("subscriptionId")),
        "operation": operation,
        **({"timing": "end_of_period"} if operation == "cancel" else {}),
    }


class BillingService:
    def __init__(
        self,
        sdk: Any,
        store: ActionStore,
        create_portal_session: Callable[[str], dict[str, Any]],
    ) -> None:
        self.sdk = sdk
        self.store = store
        self.create_portal_session = create_portal_session

    def dispatch(self, customer_id: str, path: str, body: Any) -> dict[str, Any]:
        if path == "/api/billing/session":
            fields(body, set())
            return self.create_portal_session(customer_id)
        if path == "/api/billing/cancellation/preview":
            fields(body, {"subscriptionId", "operation"})
            result = self.sdk.preview_subscription_transition(transition(customer_id, body))[
                "subscription_transition"
            ]
            return {
                "operation": result["operation"],
                "effectiveAt": date(result["effective_at"]),
                "renewalAt": date(result["renewal_at"]) if result.get("renewal_at") else None,
            }
        if path != "/api/billing/cancellation":
            raise HttpError(404, "Not found.")
        fields(body, {"subscriptionId", "operation", "idempotencyKey", "expectedEffectiveAt"})
        params = transition(customer_id, body)
        client_key = identifier(body.get("idempotencyKey"))
        expected = date(body.get("expectedEffectiveAt")) if body["operation"] == "cancel" else None
        if body["operation"] == "uncancel" and "expectedEffectiveAt" in body:
            raise HttpError(400, "Keep does not accept settlement options.")
        key = hashlib.sha256(
            json.dumps(
                [customer_id, client_key], separators=(",", ":"), ensure_ascii=False
            ).encode()
        ).hexdigest()
        fingerprint = json.dumps([params, expected], sort_keys=True)
        with self.store.lock:
            entry = self.store.entries.get(key)
            if entry and entry["fingerprint"] != fingerprint:
                raise HttpError(409, "Idempotency key already used.")
            if entry and entry["complete"]:
                return {}
            if not entry:
                current = self.sdk.preview_subscription_transition(params)[
                    "subscription_transition"
                ]
                if expected and date(current["effective_at"]) != expected:
                    raise HttpError(409, "Cancellation date changed. Preview and confirm again.")
                # Persist before sending so a lost response reuses Core's idempotency key.
                self.store.save(key, {"fingerprint": fingerprint, "complete": False})
            try:
                self.sdk.apply_subscription_transition(
                    {**params, **({"expected_effective_at": expected} if expected else {})},
                    idempotency_key=key,
                )
            except NozleAPIError as error:
                if error.status_code == 409:
                    raise HttpError(
                        409, "Subscription changed. Preview and confirm again."
                    ) from None
                raise
            self.store.save(key, {"fingerprint": fingerprint, "complete": True})
            return {}


def portal_session_reader(
    api_key: str, core_url: str, portal_api_url: str, get: Callable[..., Any] = requests.get
) -> Callable[[str], dict[str, Any]]:
    def read(customer_id: str) -> dict[str, Any]:
        response = get(
            f"{core_url.rstrip('/')}/api/v1/customers/{quote(customer_id, safe='')}/portal_url",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise RuntimeError("Session unavailable")
        url = response.json()["customer"]["portal_url"]
        match = re.search(r"/customer-portal/([^/]+)/?$", urlsplit(url).path)
        if not match:
            raise RuntimeError("Session unavailable")
        return {"token": unquote(match[1]), "apiUrl": portal_api_url}

    return read


LOGIN_PAGE = b"""<!doctype html><html lang="en"><meta charset="utf-8">
<title>Merchant billing login</title><h1>Merchant billing demo</h1>
<p>Sign in using the demo login token chosen by the server operator.</p>
<form><label>Login token <input type="password" autocomplete="off" required></label>
<button>Sign in</button></form><p role="status"></p>
<script>document.querySelector('form').onsubmit=async(e)=>{e.preventDefault();
const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({token:document.querySelector('input').value})});
document.querySelector('input').value='';document.querySelector('[role=status]').textContent=
r.ok?'Signed in. Open the React portal on this origin.':'Sign in failed.';};</script></html>"""


def merchant_handler(
    *,
    origin: str,
    login_token: str,
    customer_id: str,
    dispatch: Callable[[str, str, Any], dict[str, Any]],
    now: Callable[[], float] = time.time,
) -> type[BaseHTTPRequestHandler]:
    if len(login_token) < 32:
        raise ValueError("DEMO_LOGIN_TOKEN must contain at least 32 characters")
    sessions: dict[str, dict[str, Any]] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            pass  # Never log credentials, portal URLs, or upstream error bodies.

        def send_json(self, status: int, body: Any, cookie: str = "") -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self) -> None:
            if self.path != "/":
                self.send_json(405, {"error": "Use POST."})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(LOGIN_PAGE)

        def do_POST(self) -> None:
            try:
                if self.headers.get("Origin") != origin:
                    raise HttpError(403, "Origin not allowed.")
                if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise HttpError(415, "Use JSON.")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    raise HttpError(400, "Invalid request length.") from None
                if length < 0 or length > 4096:
                    raise HttpError(413, "Request too large.")
                try:
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    raise HttpError(400, "Invalid JSON.") from None
                if self.path == "/api/login":
                    fields(body, {"token"})
                    if not isinstance(body.get("token"), str) or not hmac.compare_digest(
                        body["token"].encode(), login_token.encode()
                    ):
                        raise HttpError(401, "Sign in failed.")
                    for identifier in list(sessions):
                        if sessions[identifier]["expires"] <= now():
                            del sessions[identifier]
                    identifier = secrets.token_hex(32)
                    sessions[identifier] = {"customer_id": customer_id, "expires": now() + 3600}
                    cookie = (
                        f"billing_session={identifier}; HttpOnly; SameSite=Strict;"
                        " Path=/; Max-Age=3600"
                        + ("; Secure" if origin.startswith("https:") else "")
                    )
                    self.send_json(200, {}, cookie)
                    return
                # Replace with merchant authentication. Never authorize a body-supplied customer.
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                cookie_value = cookies.get("billing_session")
                session = sessions.get(cookie_value.value if cookie_value else "")
                if not session or session["expires"] <= now():
                    raise HttpError(401, "Sign in again.")
                self.send_json(200, dispatch(session["customer_id"], self.path, body))
            except HttpError as error:
                self.send_json(error.status, {"error": str(error)})
            except Exception:
                self.send_json(502, {"error": "Billing request failed. Refresh before retrying."})

    return Handler


if __name__ == "__main__":
    api_key = os.environ["NOZLE_API_KEY"]
    core_url = os.environ["NOZLE_CORE_URL"]
    sdk = Nozle(api_key, base_url=os.environ["NOZLE_ENGINE_URL"], events_url=core_url)
    service = BillingService(
        sdk,
        ActionStore(Path(os.environ.get("ACTION_STORE_PATH", ".billing-portal/actions.json"))),
        portal_session_reader(
            os.environ.get("NOZLE_CORE_API_KEY") or api_key,
            core_url,
            os.environ.get("NOZLE_PORTAL_API_URL") or core_url,
        ),
    )
    handler = merchant_handler(
        origin=os.environ.get("MERCHANT_ORIGIN", "http://localhost:4243"),
        login_token=os.environ["DEMO_LOGIN_TOKEN"],
        customer_id=os.environ["DEMO_CUSTOMER_ID"],
        dispatch=service.dispatch,
    )
    server = HTTPServer(
        (os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "4243"))), handler
    )
    print("Merchant billing example ready", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        sdk.close()
