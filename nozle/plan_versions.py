"""The versions of a plan code and the migrations that move subscribers between them.

A plan is version 1 until its pricing is changed while customers pay for it; that change saves
version 2, and so on. Subscriptions stay on their version until a version migration moves them, each
at the end of its billing period. Secret keys only."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import path_segment, require_non_empty, require_secret_key
from nozle.errors import NozleAPIError, NozleValidationError
from nozle.types import Plan, PlanVersion, PlanVersionMigration, PlanVersionMigrationPreview


class PlanVersionsNamespace(Resource[M]):
    """``client.plan_versions``: a plan code's versions, and migrating subscribers between them."""

    @endpoint
    def list(self, code: str) -> Steps[List[PlanVersion]]:
        """Every version of the plan code, oldest first, with its price and active subscriptions."""
        payload = yield from self._call("plan_versions.list", "GET", f"{_plan(code)}/versions")
        return cast(List[PlanVersion], payload.get("versions") or [])

    @endpoint
    def get(self, code: str, version: int) -> Steps[Plan]:
        """One version of the plan code, for example the older version a subscriber is billed on."""
        _positive(version, "version", "plan_versions.get")
        payload = yield from self._call(
            "plan_versions.get", "GET", _plan(code), params={"version": version}
        )
        return cast(Plan, payload.get("plan"))

    @endpoint
    def preview_migration(
        self, code: str, from_version: int, to_version: Optional[int] = None
    ) -> Steps[PlanVersionMigrationPreview]:
        """What migrating ``from_version``'s subscriptions would do to each of them. Nothing is
        written."""
        operation = "plan_versions.preview_migration"
        body = _body(operation, from_version, to_version)
        payload = yield from self._call(
            operation, "POST", f"{_migrations(code)}/preview", body=body
        )
        return cast(PlanVersionMigrationPreview, payload.get("plan_version_migration_preview"))

    @endpoint
    def create_migration(
        self, code: str, from_version: int, to_version: Optional[int] = None
    ) -> Steps[PlanVersionMigration]:
        """Start moving ``from_version``'s active subscriptions to ``to_version`` (the current
        version by default). Each moves at the end of its billing period; nothing is invoiced or
        refunded now. The migration runs in the background: read it again with
        :meth:`get_migration`. Another migration of the code still running raises
        :class:`NozleAPIError` 409 with code ``plan_version_migration_running``."""
        operation = "plan_versions.create_migration"
        body = _body(operation, from_version, to_version)
        return (yield from self._migration(operation, "POST", _migrations(code), body))

    @endpoint
    def list_migrations(self, code: str) -> Steps[List[PlanVersionMigration]]:
        """The migrations of the plan code, newest first, without their items."""
        payload = yield from self._call("plan_versions.list_migrations", "GET", _migrations(code))
        return cast(List[PlanVersionMigration], payload.get("plan_version_migrations") or [])

    @endpoint
    def get_migration(self, code: str, migration_id: str) -> Steps[PlanVersionMigration]:
        """A migration with what it did to each subscription (``items``)."""
        operation = "plan_versions.get_migration"
        return (yield from self._migration(operation, "GET", _one(code, migration_id, operation)))

    @endpoint
    def cancel_migration(self, code: str, migration_id: str) -> Steps[PlanVersionMigration]:
        """Stop a migration and unschedule every move not started; those subscriptions stay on their
        version."""
        operation = "plan_versions.cancel_migration"
        path = f"{_one(code, migration_id, operation)}/cancel"
        return (yield from self._migration(operation, "POST", path))

    def _migration(
        self, operation: str, method: str, path: str, body: Optional[Dict[str, Any]] = None
    ) -> Steps[PlanVersionMigration]:
        payload = yield from self._call(operation, method, path, body=body)
        return cast(PlanVersionMigration, payload.get("plan_version_migration"))

    def _call(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict[str, Any]] = None,
    ) -> Steps[Mapping[str, Any]]:
        require_secret_key(self._api_key, operation)
        payload = yield self._transport.call(operation, method, path, params=params, json_body=body)
        if not isinstance(payload, Mapping):
            raise NozleAPIError(operation, 200, "response was not a JSON object")
        return payload


def _plan(code: str) -> str:
    require_non_empty(code, "code", "plan_versions")
    return f"/api/v1/plans/{path_segment(code)}"


def _migrations(code: str) -> str:
    return f"{_plan(code)}/version-migrations"


def _one(code: str, migration_id: str, operation: str) -> str:
    require_non_empty(migration_id, "migration_id", operation)
    return f"{_migrations(code)}/{path_segment(migration_id)}"


def _positive(value: Any, name: str, operation: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise NozleValidationError(f"{operation} requires {name} to be an integer of 1 or more")


def _body(operation: str, from_version: int, to_version: Optional[int]) -> Dict[str, Any]:
    _positive(from_version, "from_version", operation)
    body: Dict[str, Any] = {"from_version": from_version}
    if to_version is not None:
        _positive(to_version, "to_version", operation)
        body["to_version"] = to_version
    return body
