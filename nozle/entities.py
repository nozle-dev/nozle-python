from __future__ import annotations

from typing import Dict, List, Mapping, Optional, Sequence, Union, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import (
    path_segment,
    require_non_empty,
    require_secret_key,
    validate_entity_id,
    validate_entity_status,
    validate_idempotency_key,
    validate_page_limit,
)
from nozle.errors import NozleAPIError, NozleValidationError
from nozle.types import (
    CustomerEntity,
    CustomerEntityBulkMutationResult,
    CustomerEntityBulkUpsertItem,
    CustomerEntityMutationResult,
    CustomerEntityPage,
    CustomerEntityStatus,
    JSONMapping,
)


class EntitiesNamespace(Resource[M]):
    """Read and idempotently manage customer Entities."""

    @endpoint
    def get(self, customer_id: str, entity_id: str) -> Steps[CustomerEntity]:
        return (yield from self._get(customer_id, entity_id))

    def _get(self, customer_id: str, entity_id: str) -> Steps[CustomerEntity]:
        operation = "entities.get"
        require_secret_key(self._api_key, operation)
        self._validate_path(customer_id, entity_id, operation)
        payload = yield self._transport.call(
            operation,
            "GET",
            self._entity_path(customer_id, entity_id),
        )
        if not isinstance(payload, Mapping) or not isinstance(payload.get("entity"), Mapping):
            raise NozleAPIError(operation, 200, "response did not contain an entity")
        return cast(CustomerEntity, payload["entity"])

    @endpoint
    def list(
        self,
        customer_id: str,
        *,
        status: Optional[CustomerEntityStatus] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Steps[CustomerEntityPage]:
        operation = "entities.list"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        validate_page_limit(limit, operation)
        if status is not None:
            validate_entity_status(status, operation)
        params: Dict[str, Union[str, int]] = {}
        if status:
            params["status"] = status
        if limit is not None:
            params["limit"] = limit
        if cursor:
            params["cursor"] = cursor
        return cast(
            CustomerEntityPage,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    f"/api/v1/customers/{path_segment(customer_id)}/entities",
                    params=params,
                )
            ),
        )

    @endpoint
    def upsert(
        self,
        customer_id: str,
        entity_id: str,
        *,
        status: CustomerEntityStatus,
        idempotency_key: str,
        name: Optional[str] = None,
        metadata: Optional[JSONMapping] = None,
    ) -> Steps[CustomerEntityMutationResult]:
        return (
            yield from self._upsert(
                customer_id,
                entity_id,
                status=status,
                idempotency_key=idempotency_key,
                name=name,
                metadata=metadata,
            )
        )

    def _upsert(
        self,
        customer_id: str,
        entity_id: str,
        *,
        status: CustomerEntityStatus,
        idempotency_key: str,
        name: Optional[str] = None,
        metadata: Optional[JSONMapping] = None,
    ) -> Steps[CustomerEntityMutationResult]:
        operation = "entities.upsert"
        require_secret_key(self._api_key, operation)
        self._validate_path(customer_id, entity_id, operation)
        validate_entity_status(status, operation)
        validate_idempotency_key(idempotency_key, operation)
        return cast(
            CustomerEntityMutationResult,
            (
                yield self._transport.call(
                    operation,
                    "PUT",
                    self._entity_path(customer_id, entity_id),
                    json_body={"name": name, "status": status, "metadata": metadata or {}},
                    headers={"Idempotency-Key": idempotency_key},
                )
            ),
        )

    @endpoint
    def suspend(
        self,
        customer_id: str,
        entity_id: str,
        *,
        idempotency_key: str,
    ) -> Steps[CustomerEntityMutationResult]:
        operation = "entities.suspend"
        require_secret_key(self._api_key, operation)
        self._validate_path(customer_id, entity_id, operation)
        validate_idempotency_key(idempotency_key, operation)
        current = yield from self._get(customer_id, entity_id)
        return (
            yield from self._upsert(
                customer_id,
                entity_id,
                status="suspended",
                idempotency_key=idempotency_key,
                name=current.get("name"),
                metadata=current.get("metadata", {}),
            )
        )

    @endpoint
    def activate(
        self,
        customer_id: str,
        entity_id: str,
        *,
        idempotency_key: str,
    ) -> Steps[CustomerEntityMutationResult]:
        operation = "entities.activate"
        require_secret_key(self._api_key, operation)
        self._validate_path(customer_id, entity_id, operation)
        validate_idempotency_key(idempotency_key, operation)
        current = yield from self._get(customer_id, entity_id)
        return (
            yield from self._upsert(
                customer_id,
                entity_id,
                status="active",
                idempotency_key=idempotency_key,
                name=current.get("name"),
                metadata=current.get("metadata", {}),
            )
        )

    @endpoint
    def bulk_upsert(
        self,
        customer_id: str,
        entities: Sequence[CustomerEntityBulkUpsertItem],
        *,
        idempotency_key: str,
    ) -> Steps[CustomerEntityBulkMutationResult]:
        operation = "entities.bulk_upsert"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        validate_idempotency_key(idempotency_key, operation)
        if len(entities) < 1 or len(entities) > 500:
            raise NozleValidationError(f"{operation} requires between 1 and 500 entities")

        external_ids = set()
        body: List[JSONMapping] = []
        for entity in entities:
            external_id = entity.get("external_id")
            if not isinstance(external_id, str):
                raise NozleValidationError(f"{operation} requires every external_id")
            external_id = external_id.strip()
            validate_entity_id(external_id, operation)
            if external_id in external_ids:
                raise NozleValidationError(
                    f"{operation} contains duplicate external_id {external_id}"
                )
            external_ids.add(external_id)
            status = entity.get("status")
            if not isinstance(status, str):
                raise NozleValidationError(f"{operation} requires every status")
            validate_entity_status(status, operation)
            body.append(
                {
                    "external_id": external_id,
                    "name": entity.get("name"),
                    "status": status,
                    "metadata": entity.get("metadata") or {},
                }
            )

        return cast(
            CustomerEntityBulkMutationResult,
            (
                yield self._transport.call(
                    operation,
                    "POST",
                    f"/api/v1/customers/{path_segment(customer_id)}/entities/bulk-upsert",
                    json_body={"entities": body},
                    headers={"Idempotency-Key": idempotency_key},
                )
            ),
        )

    @staticmethod
    def _validate_path(customer_id: str, entity_id: str, operation: str) -> None:
        require_non_empty(customer_id, "customer_id", operation)
        validate_entity_id(entity_id, operation)

    @staticmethod
    def _entity_path(customer_id: str, entity_id: str) -> str:
        return f"/api/v1/customers/{path_segment(customer_id)}/entities/{path_segment(entity_id)}"
