from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional, TypedDict, Union

JSONMapping = Dict[str, Any]


class TrackOptions(TypedDict, total=False):
    subscription_id: str
    transaction_id: str
    timestamp: str


class CostEventAccepted(TypedDict):
    status: Literal["accepted"]
    cost_event_id: str


class _CanResultRequired(TypedDict):
    allowed: bool
    used: int


class CanEconomics(TypedDict, total=False):
    status: Literal["estimated", "not_configured", "unavailable", "stale"]
    currency: str
    estimated_incremental_cost: str
    estimated_incremental_revenue: str
    estimated_incremental_margin: str
    estimated_margin_percent: str
    rule_version_ids: List[str]
    includes: List[str]
    excludes: List[str]
    reason: str
    calculated_at: str


class CanPolicy(TypedDict, total=False):
    version_id: str
    mode: Literal["allow", "warn", "deny"]
    on_unavailable: Literal["allow", "warn", "deny"]
    minimum_margin_amount: str
    minimum_margin_percent: str
    maximum_estimated_cost: str
    maximum_snapshot_age_seconds: int
    decision: Literal["allow", "warn", "deny"]
    reason: str


class CanResult(_CanResultRequired, total=False):
    reason: str
    feature_type: str
    configuration: Dict[str, str]
    limit: int
    remaining: int
    overage: bool
    economics: CanEconomics
    policy: CanPolicy


class Plan(TypedDict):
    code: str
    name: str
    amount_cents: int
    amount_currency: str
    interval: str


class _StripeCheckoutRequired(TypedDict):
    type: Literal["stripe"]


class StripeCheckoutResult(_StripeCheckoutRequired, total=False):
    client_secret: str
    clientSecret: str
    url: str
    invoice_id: str
    amount_cents: int
    currency: str
    external_entity_id: str
    external_subscription_id: str


class _CompletedCheckoutRequired(TypedDict):
    type: Literal["completed"]
    status: str


class CompletedCheckoutResult(_CompletedCheckoutRequired, total=False):
    subscription_id: str
    plan_code: str
    external_entity_id: str
    external_subscription_id: str


class _ScheduledCheckoutRequired(TypedDict):
    type: Literal["scheduled"]
    status: str


class ScheduledCheckoutResult(_ScheduledCheckoutRequired, total=False):
    subscription_id: str
    plan_code: str
    external_entity_id: str
    external_subscription_id: str


class _RazorpayCheckoutRequired(TypedDict):
    type: Literal["razorpay"]
    checkout_id: str
    key_id: str
    order_id: str
    amount_cents: int
    currency: str


class RazorpayCheckoutResult(_RazorpayCheckoutRequired, total=False):
    invoice_id: str
    expires_at: str
    customer_id: str
    recurring: bool
    mandate_max_amount_cents: int


class ProcessingCheckoutResult(TypedDict):
    type: Literal["processing"]
    checkout_id: str
    status: str


class HostedCheckoutResult(TypedDict):
    type: Literal["hosted"]
    payment_url: str


class RazorpayVerification(TypedDict):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


CheckoutResult = Union[
    StripeCheckoutResult,
    CompletedCheckoutResult,
    ScheduledCheckoutResult,
    RazorpayCheckoutResult,
    ProcessingCheckoutResult,
    HostedCheckoutResult,
]


class _CheckoutStatusRequired(TypedDict):
    checkout_id: str
    provider: Literal["razorpay"]
    status: Literal[
        "processing", "awaiting_payment", "succeeded", "failed", "expired", "needs_review"
    ]
    fulfillment_status: Literal["pending", "processing", "succeeded"]
    amount_cents: int
    currency: str


class CheckoutStatus(_CheckoutStatusRequired, total=False):
    invoice_id: Optional[str]
    checkout: CheckoutResult


class EntitySubscriptionPlan(TypedDict):
    code: str
    name: str
    interval: str
    amount_cents: int
    amount_currency: str
    status: str
    effective_at: Optional[str]


class EntitySubscription(TypedDict):
    external_customer_id: str
    external_entity_id: str
    external_subscription_id: str
    status: str
    current_plan: Optional[EntitySubscriptionPlan]
    pending_plan: Optional[EntitySubscriptionPlan]
    billing_time: Optional[str]
    subscription_at: Optional[str]
    started_at: Optional[str]
    current_period_start: Optional[str]
    current_period_end: Optional[str]
    ending_at: Optional[str]
    canceled_at: Optional[str]
    cancel_at_period_end: Optional[bool]
    created_at: str
    updated_at: str


class EntitySubscriptionList(TypedDict):
    entity_subscriptions: List[EntitySubscription]


class EntitySubscriptionCheckoutItem(TypedDict):
    external_entity_id: str
    plan_code: str


class EntitySubscriptionCheckoutItemResult(TypedDict):
    external_entity_id: str
    external_subscription_id: str
    plan_code: str
    subscription_status: str


class _EntitySubscriptionCheckoutManyRequired(TypedDict):
    id: str
    type: Literal["stripe", "razorpay", "processing", "completed"]
    status: str
    client_secret: Optional[str]
    invoice_id: str
    amount_cents: int
    currency: str
    replayed: bool
    expires_at: Optional[str]
    items: List[EntitySubscriptionCheckoutItemResult]


class EntitySubscriptionCheckoutManyResult(_EntitySubscriptionCheckoutManyRequired, total=False):
    checkout_id: str
    key_id: str
    order_id: str


class SubscribeResult(TypedDict):
    subscription_id: str
    status: str


CancellationPolicy = Literal["end_of_period", "immediate"]


class _SubscriptionCancellationRequired(TypedDict):
    external_id: str
    status: str


class SubscriptionCancellation(_SubscriptionCancellationRequired, total=False):
    ending_at: Optional[str]
    terminated_at: Optional[str]


class CancelSubscriptionResult(TypedDict):
    subscription: SubscriptionCancellation


SubscriptionTransitionOperation = Literal["cancel", "downgrade", "uncancel"]
SubscriptionTransitionTiming = Literal["end_of_period", "immediate"]
SubscriptionTransitionBillingAnchor = Literal["keep_anchor", "reset_anchor"]
SubscriptionTransitionProrationBehavior = Literal["prorate_immediately", "none"]
SubscriptionTransitionCreditAction = Literal["credit", "refund", "offset", "none"]
SubscriptionTransitionRefundMode = Literal["prorated", "full"]
SubscriptionTransitionFinalInvoiceAction = Literal["generate", "skip"]


class SubscriptionTransitionParams(TypedDict, total=False):
    customer_id: str
    subscription_id: str
    operation: SubscriptionTransitionOperation
    timing: SubscriptionTransitionTiming
    target_plan_code: str
    billing_anchor: SubscriptionTransitionBillingAnchor
    proration_behavior: SubscriptionTransitionProrationBehavior
    credit_action: SubscriptionTransitionCreditAction
    refund_mode: SubscriptionTransitionRefundMode
    final_invoice_action: SubscriptionTransitionFinalInvoiceAction


class SubscriptionTransitionPreviewBody(TypedDict, total=False):
    operation: SubscriptionTransitionOperation
    timing: SubscriptionTransitionTiming
    billing_anchor: SubscriptionTransitionBillingAnchor
    proration_behavior: SubscriptionTransitionProrationBehavior
    credit_action: SubscriptionTransitionCreditAction
    refund_mode: SubscriptionTransitionRefundMode
    final_invoice_action: SubscriptionTransitionFinalInvoiceAction
    effective_at: str
    renewal_at: Optional[str]
    credit_amount_cents: int
    refund_amount_cents: int
    offset_amount_cents: int
    amount_due_cents: int
    currency: str


class SubscriptionTransitionPreview(TypedDict):
    subscription_transition: SubscriptionTransitionPreviewBody


class SubscriptionTransitionResultBody(SubscriptionTransitionPreviewBody, total=False):
    id: str
    replayed: bool
    subscription_id: str
    external_subscription_id: str
    plan_code: str
    status: str
    credit_note_id: str
    invoice_id: str


class SubscriptionTransitionResult(TypedDict):
    subscription_transition: SubscriptionTransitionResultBody


class EntitySubscriptionCancelResult(TypedDict):
    entity_subscription: EntitySubscription
    subscription_transition: SubscriptionTransitionResultBody


class _PingResultRequired(TypedDict):
    ok: bool
    engine: str


class PingResult(_PingResultRequired, total=False):
    version: str


class _CustomerUpsertResultRequired(TypedDict):
    external_id: str


class CustomerUpsertResult(_CustomerUpsertResultRequired, total=False):
    name: str
    email: str


class CheckAndDeductResult(TypedDict):
    allowed: bool
    remaining: Union[int, float]


class CreditSystem(TypedDict):
    id: str
    code: str
    name: str
    description: Optional[str]
    unit_name: str
    status: str
    created_at: str
    updated_at: str


CreditBalanceSourceType = Literal[
    "subscription_grant",
    "top_up",
    "manual_grant",
    "adjustment",
    "allocated_top_up",
]


class CreditBalanceSource(TypedDict):
    id: str
    entity_id: Optional[str]
    parent_source_id: Optional[str]
    scope: Literal["customer", "entity"]
    transferable: bool
    returnable: bool
    type: CreditBalanceSourceType
    reference: str
    subscription_id: Optional[str]
    initial: str
    remaining: str
    valid_from: str
    expires_at: Optional[str]
    priority: int
    status: str
    available: bool


class CreditBalance(TypedDict):
    customer_id: str
    credit_system: str
    credit_system_id: str
    credit_system_name: str
    unit_name: str
    system_status: str
    available: str
    as_of: str
    sources: List[CreditBalanceSource]


class CreditBalances(TypedDict):
    customer_id: str
    as_of: str
    balances: List[CreditBalance]


class CreditOperationAllocation(TypedDict):
    source_id: str
    source_entity_id: Optional[str]
    source_type: str
    delta: str
    before: str
    after: str


CreditOperationType = Literal[
    "consume", "grant", "adjustment", "expire", "revoke", "refund", "transfer"
]
CreditOperationStatus = Literal["succeeded", "denied", "reversed"]


class CreditOperation(TypedDict):
    id: str
    entity_id: Optional[str]
    credit_system: str
    credit_system_id: str
    credit_system_name: str
    unit_name: str
    feature_code: Optional[str]
    type: CreditOperationType
    status: CreditOperationStatus
    metric_amount: Optional[str]
    credit_amount: str
    rate_id: Optional[str]
    rate_metric_amount: Optional[str]
    rate_credit_amount: Optional[str]
    reason: Optional[str]
    occurred_at: str
    source_allocations: List[CreditOperationAllocation]


class CreditOperationPage(TypedDict):
    customer_id: str
    operations: List[CreditOperation]
    next_cursor: Optional[str]


CustomerEntityStatus = Literal["active", "suspended", "deleted"]


class CustomerEntity(TypedDict):
    id: str
    customer_id: str
    external_id: str
    name: Optional[str]
    status: CustomerEntityStatus
    metadata: JSONMapping
    created_at: str
    updated_at: str
    deleted_at: Optional[str]


class _CustomerEntityUpsertRequired(TypedDict):
    status: CustomerEntityStatus


class CustomerEntityUpsertData(_CustomerEntityUpsertRequired, total=False):
    name: Optional[str]
    metadata: JSONMapping


class CustomerEntityBulkUpsertItem(CustomerEntityUpsertData):
    external_id: str


CustomerEntityAction = Literal[
    "created", "updated", "unchanged", "activated", "reactivated", "suspended", "deleted"
]


class CustomerEntityMutationResult(TypedDict):
    action: CustomerEntityAction
    entity: CustomerEntity
    replayed: bool


class CustomerEntityPage(TypedDict):
    customer_id: str
    entities: List[CustomerEntity]
    next_cursor: Optional[str]


class CustomerEntityBulkMutationCounts(TypedDict):
    created: int
    updated: int
    unchanged: int
    activated: int
    reactivated: int
    suspended: int
    deleted: int


class CustomerEntityBulkMutationItem(TypedDict):
    action: CustomerEntityAction
    entity: CustomerEntity


class CustomerEntityBulkMutationResult(TypedDict):
    customer_id: str
    entities: List[CustomerEntityBulkMutationItem]
    counts: CustomerEntityBulkMutationCounts
    replayed: bool


EntityCreditPoolPolicy = Literal["entity_only", "entity_then_customer", "customer_only"]


class EntityCreditBalance(TypedDict):
    customer_id: str
    entity_id: str
    entity_status: CustomerEntityStatus
    credit_system: str
    credit_system_id: str
    credit_system_name: str
    unit_name: str
    system_status: str
    entity_available: str
    shared_available: str
    effective_available: str
    consumed: str
    pool_policy: Optional[EntityCreditPoolPolicy]
    as_of: str
    sources: List[CreditBalanceSource]


class EntityCreditBalances(TypedDict):
    customer_id: str
    entity_id: str
    entity_status: CustomerEntityStatus
    as_of: str
    balances: List[EntityCreditBalance]


class EntityCreditOperationPage(CreditOperationPage):
    entity_id: str


class _EntityCreditTransferSourceRequired(TypedDict):
    source_id: str
    source_type: Literal["top_up", "allocated_top_up"]
    scope: Literal["customer", "entity"]
    amount: str
    before: str
    after: str
    expires_at: Optional[str]


class EntityCreditTransferSource(_EntityCreditTransferSourceRequired, total=False):
    parent_source_id: Optional[str]
    created: bool


class _EntityCreditTransferResultRequired(TypedDict):
    transferred: bool
    operation_id: str
    customer_id: str
    entity_id: str
    credit_system: str
    direction: Literal["allocation", "deallocation"]
    amount: str
    available: str
    parent_sources: List[EntityCreditTransferSource]
    entity_sources: List[EntityCreditTransferSource]
    replayed: bool


class EntityCreditTransferResult(_EntityCreditTransferResultRequired, total=False):
    reason: str


class _UsageDeductionRequired(TypedDict):
    source_type: str
    source_scope: str
    amount: str
    remaining: str


class UsageDeduction(_UsageDeductionRequired, total=False):
    balance_source_id: str


class _UsageCheckResultRequired(TypedDict):
    advisory: Literal[True]
    allowed: bool
    metric_amount: str
    credit_system: str
    credits_required: str
    available: str


class UsageCheckResult(_UsageCheckResultRequired, total=False):
    entity_id: str
    pool_policy: str
    projected_remaining: str
    projected_deductions: List[UsageDeduction]
    reason: str


class _UsageTrackResultRequired(TypedDict):
    allowed: bool


class UsageTrackResult(_UsageTrackResultRequired, total=False):
    operation_id: str
    entity_id: str
    pool_policy: str
    metric_amount: str
    credit_system: str
    credits_required: str
    credits_consumed: str
    available: str
    remaining: str
    reason: str
    deductions: List[UsageDeduction]
