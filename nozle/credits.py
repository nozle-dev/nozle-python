from __future__ import annotations

from typing import Dict, Optional, Union, cast

from nozle._operation import M, Resource, Steps, endpoint
from nozle._validation import (
    path_segment,
    require_non_empty,
    require_secret_key,
    validate_entity_id,
    validate_idempotency_key,
    validate_page_limit,
    validate_transfer_amount,
)
from nozle.types import (
    CreditBalance,
    CreditBalances,
    CreditOperationPage,
    EntityCreditBalance,
    EntityCreditBalances,
    EntityCreditOperationPage,
    EntityCreditTransferResult,
)


class CreditsNamespace(Resource[M]):
    """Read customer and Entity ledgers and transfer top-up credits."""

    @endpoint
    def get_balance(self, customer_id: str, credit_system_code: str) -> Steps[CreditBalance]:
        operation = "credits.get_balance"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        require_non_empty(credit_system_code, "credit_system_code", operation)
        return cast(
            CreditBalance,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    (
                        f"/api/v1/customers/{path_segment(customer_id)}/credit-systems/"
                        f"{path_segment(credit_system_code)}/balance"
                    ),
                )
            ),
        )

    @endpoint
    def list_balances(self, customer_id: str) -> Steps[CreditBalances]:
        operation = "credits.list_balances"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        return cast(
            CreditBalances,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    f"/api/v1/customers/{path_segment(customer_id)}/credit-systems",
                )
            ),
        )

    @endpoint
    def list_operations(
        self,
        customer_id: str,
        *,
        credit_system_code: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Steps[CreditOperationPage]:
        operation = "credits.list_operations"
        require_secret_key(self._api_key, operation)
        require_non_empty(customer_id, "customer_id", operation)
        validate_page_limit(limit, operation)
        params: Dict[str, Union[str, int]] = {}
        if credit_system_code:
            params["credit_system_code"] = credit_system_code
        if limit is not None:
            params["limit"] = limit
        if cursor:
            params["cursor"] = cursor
        return cast(
            CreditOperationPage,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    f"/api/v1/customers/{path_segment(customer_id)}/credit-operations",
                    params=params,
                )
            ),
        )

    @endpoint
    def get_entity_balance(
        self,
        customer_id: str,
        entity_id: str,
        credit_system_code: str,
    ) -> Steps[EntityCreditBalance]:
        operation = "credits.get_entity_balance"
        require_secret_key(self._api_key, operation)
        self._validate_entity_path(customer_id, entity_id, operation)
        require_non_empty(credit_system_code, "credit_system_code", operation)
        return cast(
            EntityCreditBalance,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    (
                        f"{self._entity_credit_path(customer_id, entity_id)}/"
                        f"{path_segment(credit_system_code)}/balance"
                    ),
                )
            ),
        )

    @endpoint
    def list_entity_balances(
        self,
        customer_id: str,
        entity_id: str,
    ) -> Steps[EntityCreditBalances]:
        operation = "credits.list_entity_balances"
        require_secret_key(self._api_key, operation)
        self._validate_entity_path(customer_id, entity_id, operation)
        return cast(
            EntityCreditBalances,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    self._entity_credit_path(customer_id, entity_id),
                )
            ),
        )

    @endpoint
    def list_entity_operations(
        self,
        customer_id: str,
        entity_id: str,
        *,
        credit_system_code: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> Steps[EntityCreditOperationPage]:
        operation = "credits.list_entity_operations"
        require_secret_key(self._api_key, operation)
        self._validate_entity_path(customer_id, entity_id, operation)
        validate_page_limit(limit, operation)
        params: Dict[str, Union[str, int]] = {}
        if credit_system_code:
            params["credit_system_code"] = credit_system_code
        if limit is not None:
            params["limit"] = limit
        if cursor:
            params["cursor"] = cursor
        return cast(
            EntityCreditOperationPage,
            (
                yield self._transport.call(
                    operation,
                    "GET",
                    (
                        f"/api/v1/customers/{path_segment(customer_id)}/entities/"
                        f"{path_segment(entity_id)}/credit-operations"
                    ),
                    params=params,
                )
            ),
        )

    @endpoint
    def allocate(
        self,
        customer_id: str,
        entity_id: str,
        *,
        credit_system_code: str,
        amount: str,
        idempotency_key: str,
    ) -> Steps[EntityCreditTransferResult]:
        return (
            yield from self._transfer(
                "allocate",
                customer_id,
                entity_id,
                credit_system_code,
                amount,
                idempotency_key,
            )
        )

    @endpoint
    def deallocate(
        self,
        customer_id: str,
        entity_id: str,
        *,
        credit_system_code: str,
        amount: str,
        idempotency_key: str,
    ) -> Steps[EntityCreditTransferResult]:
        return (
            yield from self._transfer(
                "deallocate",
                customer_id,
                entity_id,
                credit_system_code,
                amount,
                idempotency_key,
            )
        )

    def _transfer(
        self,
        action: str,
        customer_id: str,
        entity_id: str,
        credit_system_code: str,
        amount: str,
        idempotency_key: str,
    ) -> Steps[EntityCreditTransferResult]:
        operation = f"credits.{action}"
        require_secret_key(self._api_key, operation)
        self._validate_entity_path(customer_id, entity_id, operation)
        require_non_empty(credit_system_code, "credit_system_code", operation)
        validate_transfer_amount(amount, operation)
        validate_idempotency_key(idempotency_key, operation)
        suffix = "credit-allocations" if action == "allocate" else "credit-deallocations"
        return cast(
            EntityCreditTransferResult,
            (
                yield self._transport.call(
                    operation,
                    "POST",
                    (
                        f"/api/v1/customers/{path_segment(customer_id)}/entities/"
                        f"{path_segment(entity_id)}/{suffix}"
                    ),
                    json_body={"credit_system": credit_system_code, "amount": amount},
                    headers={"Idempotency-Key": idempotency_key},
                )
            ),
        )

    @staticmethod
    def _validate_entity_path(customer_id: str, entity_id: str, operation: str) -> None:
        require_non_empty(customer_id, "customer_id", operation)
        validate_entity_id(entity_id, operation)

    @staticmethod
    def _entity_credit_path(customer_id: str, entity_id: str) -> str:
        return (
            f"/api/v1/customers/{path_segment(customer_id)}/entities/"
            f"{path_segment(entity_id)}/credit-systems"
        )
