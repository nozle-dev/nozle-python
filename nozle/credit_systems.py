from __future__ import annotations

from typing import Any, List, Mapping

from nozle._transport import HttpTransport
from nozle._validation import require_secret_key
from nozle.errors import NozleAPIError
from nozle.types import CreditSystem


class CreditSystemsNamespace:
    """Read all active Credit Systems from Core, following every page."""

    def __init__(self, transport: HttpTransport, api_key: str) -> None:
        self._transport = transport
        self._api_key = api_key

    def list(self) -> List[CreditSystem]:
        operation = "credit_systems.list"
        require_secret_key(self._api_key, operation)
        systems: List[CreditSystem] = []
        page = 1

        while page > 0:
            payload = self._transport.request(
                operation,
                "GET",
                "/api/v1/credit-systems",
                params={"status": "active", "page": page, "per_page": 100},
            )
            if not isinstance(payload, Mapping):
                raise NozleAPIError(operation, 200, "response was not a JSON object")
            raw_systems = payload.get("credit_systems")
            if raw_systems is None:
                raw_systems = []
            if not isinstance(raw_systems, list):
                raise NozleAPIError(operation, 200, "credit_systems was not a list")
            for raw_system in raw_systems:
                if not isinstance(raw_system, Mapping):
                    raise NozleAPIError(operation, 200, "credit system was not an object")
                systems.append(_normalize_credit_system(raw_system))

            raw_meta = payload.get("meta")
            next_page: Any = raw_meta.get("next_page") if isinstance(raw_meta, Mapping) else None
            if next_page is None:
                page = 0
            elif type(next_page) is int and next_page > 0:
                page = next_page
            else:
                raise NozleAPIError(operation, 200, "meta.next_page was invalid")

        return systems


def _normalize_credit_system(system: Mapping[str, Any]) -> CreditSystem:
    operation = "credit_systems.list"
    system = {**system, "nozle_id": system.get("nozle_id", system.get("lago_id"))}  # lago_id: old servers
    required = ("nozle_id", "code", "name", "unit_name", "status", "created_at", "updated_at")
    if any(not isinstance(system.get(field), str) for field in required):
        raise NozleAPIError(operation, 200, "credit system response was incomplete")
    description = system.get("description")
    if description is not None and not isinstance(description, str):
        raise NozleAPIError(operation, 200, "credit system description was invalid")
    return {
        "id": system["nozle_id"],
        "code": system["code"],
        "name": system["name"],
        "description": description,
        "unit_name": system["unit_name"],
        "status": system["status"],
        "created_at": system["created_at"],
        "updated_at": system["updated_at"],
    }
