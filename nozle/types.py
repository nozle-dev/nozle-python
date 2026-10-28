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
    # Engine always sends both; None means the feature has no usage limit (unlimited).
    limit: Optional[int]
    remaining: Optional[int]
    overage: bool
    economics: CanEconomics
    policy: CanPolicy


class _PlanRequired(TypedDict):
    code: str
    name: str
    amount_cents: int
    amount_currency: str
    interval: str


class Plan(_PlanRequired, total=False):
    #: The version of the code this plan is (plan versioning). ``plans()`` returns current versions
    #: only.
    version: int
    #: Whether new subscriptions get this version.
    is_current: bool


class _CompletedCheckoutRequired(TypedDict):
    type: Literal["completed"]
    status: str


class CompletedCheckoutResult(_CompletedCheckoutRequired, total=False):
    checkout_id: str
    effective_at: str
    renewal_at: Optional[str]
    amount_due_cents: Union[int, str]
    currency: str
    payment_source: str
    invoice_id: str
    amount_cents: int
    subscription_id: str
    plan_code: str
    external_entity_id: str
    external_subscription_id: str


class _ScheduledCheckoutRequired(TypedDict):
    type: Literal["scheduled"]
    status: str


class ScheduledCheckoutResult(_ScheduledCheckoutRequired, total=False):
    transition_direction: str
    transition_policy: str
    subscription_id: str
    plan_code: str
    external_entity_id: str
    external_subscription_id: str
    checkout_id: str
    effective_at: str
    renewal_at: Optional[str]
    amount_due_cents: Union[int, str]
    currency: str


class SubscriptionPlan(TypedDict):
    code: str
    name: str
    amount_cents: Union[int, str]
    currency: str
    interval: str


class _SubscriptionIdentity(TypedDict):
    id: str
    external_id: str
    plan_code: str
    status: str


class ManagedSubscription(_SubscriptionIdentity):
    ending_at: Optional[str]
    plan: SubscriptionPlan


class PendingSubscriptionChange(TypedDict):
    id: str
    plan_code: str
    name: str
    effective_at: str
    plan: SubscriptionPlan


class EligibleSubscriptionPlan(SubscriptionPlan):
    direction: Literal["upgrade", "downgrade"]
    timing: Literal["immediate", "end_of_period"]


class SubscriptionCheckoutSummary(TypedDict):
    id: str
    status: str
    plan_code: str


class _SubscriptionOptionsRequired(TypedDict):
    subscription: ManagedSubscription
    pending_change: Optional[PendingSubscriptionChange]
    eligible_plans: List[EligibleSubscriptionPlan]


class SubscriptionOptions(_SubscriptionOptionsRequired, total=False):
    checkout: Optional[SubscriptionCheckoutSummary]


class SubscriptionChangePreview(TypedDict):
    quote_id: str
    currency: str
    credit_amount_cents: Union[int, str]
    debit_amount_cents: Union[int, str]
    net_amount_cents: Union[int, str]
    amount_due_now_cents: Union[int, str]
    amount_due_at_effective_cents: Union[int, str]
    transition_direction: Literal["upgrade", "downgrade"]
    timing: Literal["immediate", "end_of_period"]
    effective_at: str
    renewal_at: Optional[str]


class WithdrawPendingSubscriptionChangeResult(TypedDict):
    subscription: _SubscriptionIdentity
    withdrawn_pending_subscription_id: str
    replayed: bool


class _RazorpayCheckoutRequired(TypedDict):
    type: Literal["razorpay"]
    checkout_id: str
    key_id: str
    order_id: str
    amount_cents: int
    currency: str


class RazorpayCheckoutResult(_RazorpayCheckoutRequired, total=False):
    invoice_id: str
    plan_upgrade_intent_id: str
    external_entity_id: str
    external_subscription_id: str
    expires_at: str
    customer_id: str
    recurring: bool
    mandate_max_amount_cents: int


class _ProcessingCheckoutRequired(TypedDict):
    type: Literal["processing"]
    checkout_id: str
    status: str


class ProcessingCheckoutResult(_ProcessingCheckoutRequired, total=False):
    """Not ready to collect yet: Razorpay while the order is created, or Stripe when a
    quoted upgrade is replayed while its webhook is pending. Recover with
    checkout_status(checkout_id)."""

    amount_cents: int
    currency: str
    expires_at: str
    invoice_id: str
    plan_upgrade_intent_id: str


class _HostedCheckoutRequired(TypedDict):
    type: Literal["hosted"]
    url: str
    payment_url: str


class HostedCheckoutResult(_HostedCheckoutRequired, total=False):
    """A hosted page (Stripe Checkout or a Razorpay link): redirect the customer to url."""

    checkout_id: str
    amount_cents: int
    currency: str
    expires_at: str
    invoice_id: str
    plan_upgrade_intent_id: str
    external_entity_id: str
    external_subscription_id: str


class RazorpayVerification(TypedDict):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


CheckoutResult = Union[
    CompletedCheckoutResult,
    ScheduledCheckoutResult,
    RazorpayCheckoutResult,
    ProcessingCheckoutResult,
    HostedCheckoutResult,
]


class ManagePaymentMethodResult(TypedDict):
    """Stripe only: redirect the customer to `url`.

    Razorpay customers pay each invoice by payment link, so for them
    `manage_payment_method` raises `NozleAPIError` (HTTP 422) where
    `has_code("razorpay_pays_by_link")` is true.
    """

    url: str
    expires_at: Optional[str]
    provider: Literal["stripe"]


class _CheckoutStatusRequired(TypedDict):
    checkout_id: str
    provider: Literal["razorpay", "stripe"]
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
    type: Literal["hosted", "razorpay", "processing", "completed"]
    status: str
    invoice_id: str
    amount_cents: int
    currency: str
    replayed: bool
    expires_at: Optional[str]
    items: List[EntitySubscriptionCheckoutItemResult]


class EntitySubscriptionCheckoutManyResult(_EntitySubscriptionCheckoutManyRequired, total=False):
    #: Hosted result: redirect the customer here.
    url: str
    payment_url: str
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
    expected_effective_at: str
    quote_id: str


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


class PingResult(TypedDict):
    ok: bool
    engine: str


class _CustomerBillingConfigurationRequired(TypedDict):
    payment_provider: str


class CustomerBillingConfiguration(_CustomerBillingConfigurationRequired, total=False):
    """Payment provider assignment. Set ``provider_customer_id`` to link an existing
    provider customer (e.g. a Stripe ``cus_…``) without creating a new one."""

    payment_provider_code: str
    provider_customer_id: str
    sync_with_provider: bool
    provider_payment_methods: list[str]


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


class _CustomerUsageRequired(TypedDict):
    from_datetime: str
    to_datetime: str
    issuing_date: str
    amount_cents: int
    taxes_amount_cents: int
    total_amount_cents: int
    # Each item has units, amount_cents, amount_currency, charge and feature (API reference).
    charges_usage: List[Dict[str, Any]]


class CustomerUsage(_CustomerUsageRequired, total=False):
    """Usage of one billing period: Core ``current_usage`` or one ``past_usage`` item."""

    currency: str
    invoice_id: Optional[str]


class _InvoiceRequired(TypedDict):
    nozle_id: str
    number: str
    issuing_date: str
    status: str
    payment_status: str
    currency: str
    fees_amount_cents: int
    taxes_amount_cents: int
    total_amount_cents: int


class Invoice(_InvoiceRequired, total=False):
    """An invoice. The API reference lists every field; these are the ones most code reads."""

    payment_due_date: Optional[str]
    payment_overdue: bool
    file_url: Optional[str]
    customer: Dict[str, Any]


class InvoicePage(TypedDict):
    invoices: List[Invoice]
    meta: Dict[str, Any]


class AlertThreshold(TypedDict):
    code: Optional[str]
    value: str
    recurring: bool


class Alert(TypedDict, total=False):
    """A usage or wallet-balance alert."""

    nozle_id: str
    alert_type: str
    code: str
    name: Optional[str]
    external_subscription_id: Optional[str]
    wallet_code: Optional[str]
    direction: str
    previous_value: Any
    last_processed_at: Optional[str]
    thresholds: List[AlertThreshold]
    feature: Optional[Dict[str, Any]]
    created_at: str


class AlertPage(TypedDict):
    alerts: List[Alert]
    meta: Dict[str, Any]


class PlanVersion(TypedDict):
    """One version of a plan code, with its price and active subscriptions."""

    nozle_id: str
    version: int
    is_current: bool
    created_at: str
    amount_cents: int
    amount_currency: str
    active_subscriptions_count: int


class PlanVersionMigrationItem(TypedDict, total=False):
    """What a migration did to one subscription. ``move_status`` says where a scheduled move stands:
    ``pending``, ``moved`` or ``canceled``."""

    external_subscription_id: str
    status: Literal["succeeded", "skipped", "failed", "canceled"]
    skip_reason: Optional[str]
    error: Optional[str]
    effective_at: Optional[str]
    move_status: Optional[Literal["pending", "moved", "canceled"]]


class PlanVersionMigration(TypedDict, total=False):
    """A migration of one version's subscriptions to another. ``items`` only from
    ``get_migration``."""

    nozle_id: str
    plan_code: str
    from_version: int
    to_version: int
    status: Literal["running", "succeeded", "completed_with_errors", "failed", "canceled"]
    matched_count: int
    succeeded_count: int
    skipped_count: int
    failed_count: int
    started_at: Optional[str]
    finished_at: Optional[str]
    canceled_at: Optional[str]
    created_at: str
    items: List[PlanVersionMigrationItem]


class PlanVersionMigrationPreviewItem(TypedDict, total=False):
    """One subscription of a preview: ``outcome`` ``move`` (on ``effective_on``) or ``skip`` with a
    reason."""

    external_subscription_id: str
    external_customer_id: str
    outcome: Literal["move", "skip"]
    skip_reason: Optional[str]
    effective_on: Optional[str]
    current_amount_cents: int
    new_amount_cents: int


class PlanVersionMigrationPreview(TypedDict):
    plan_code: str
    from_version: int
    to_version: int
    items: List[PlanVersionMigrationPreviewItem]


class MarginSummary(TypedDict):
    """``GET /margin/summary``: amounts are integer cents."""

    total_revenue_cents: int
    total_cost_cents: int
    total_margin_cents: int
    margin_percent: float
    event_count: int


class MarginCustomerRow(TypedDict):
    customer_id: str
    customer_name: str
    revenue_cents: int
    cost_cents: int
    margin_cents: int
    event_count: int


class MarginMetricRow(TypedDict):
    metric_code: str
    revenue_cents: int
    cost_cents: int
    margin_cents: int
    event_count: int


class MarginPlanRow(TypedDict):
    plan_id: str
    revenue_cents: int
    cost_cents: int
    margin_cents: int
    event_count: int


class MarginModelRow(TypedDict):
    model: str
    revenue_cents: int
    cost_cents: int
    margin_cents: int
    event_count: int


class MarginByCustomer(TypedDict):
    """``GET /margin/customers``: ``customers`` is None when there are none."""

    customers: Optional[List[MarginCustomerRow]]


class MarginByMetric(TypedDict):
    """``GET /margin/metrics``: ``metrics`` is None when there are none."""

    metrics: Optional[List[MarginMetricRow]]


class MarginByPlan(TypedDict):
    """``GET /margin/plans``: ``plans`` is None when there are none."""

    plans: Optional[List[MarginPlanRow]]


class MarginByModel(TypedDict):
    """``GET /margin/models``: ``models`` is None when there are none."""

    models: Optional[List[MarginModelRow]]


class MarginTrendPoint(TypedDict):
    period: str
    revenue_cents: int
    cost_cents: int
    margin_cents: int
    margin_percent: float
    event_count: int


class MarginTrend(TypedDict):
    """``GET /margin/trend``: ``trend`` is None when the range has no events."""

    granularity: str
    trend: Optional[List[MarginTrendPoint]]


class MarginForecastPoint(TypedDict):
    date: str
    projected_revenue: int
    projected_cost: int
    projected_margin: int


class MarginForecast(TypedDict):
    """``GET /margin/forecast``: daily projection for the 30 days after the range (cents)."""

    points: List[MarginForecastPoint]
    confidence: Literal["low", "medium", "high"]


class MarginSimulation(TypedDict):
    """``POST /margin/simulate``: one metric's usage replayed at a new unit price (cents)."""

    metric_code: str
    current_revenue_cents: int
    current_cost_cents: int
    current_margin_cents: int
    new_revenue_cents: int
    new_cost_cents: int
    new_margin_cents: int
    revenue_delta_cents: int
    margin_delta_cents: int
    event_count: int


# ``GET /cost-intelligence/report``: ledger-backed cost totals, breakdowns and review queues.
# Functional syntax because ``from`` is a Python keyword.
CostIntelligenceReport = TypedDict(
    "CostIntelligenceReport",
    {
        "from": str,
        "to": str,
        "summary": JSONMapping,
        "features": List[JSONMapping],
        "customers": List[JSONMapping],
        "cost_events": List[JSONMapping],
        "shared_costs": List[JSONMapping],
        "uncosted": List[JSONMapping],
        "conflicts": List[JSONMapping],
    },
)


class SharedCostCustomerAllocation(TypedDict):
    external_customer_id: str
    amount: str


class SharedCostAllocationPreview(TypedDict):
    allocation_period_start: str
    allocation_period_end: str
    eligibility_period_start: str
    eligibility_period_end: str
    recognized_amount: str
    currency: str
    consumer_count: int
    allocations: List[SharedCostCustomerAllocation]


class SharedCostAllocationRun(TypedDict):
    id: str
    status: Literal["completed", "unallocated"]
    allocation_period_start: str
    allocation_period_end: str
    recognized_amount: str
    currency: str
    consumer_count: int


class _CreditBalanceImportRequestRequired(TypedDict):
    external_subscription_id: str
    credit_system_code: str
    granted_amount: str
    remaining_amount: str


class CreditBalanceImportRequest(_CreditBalanceImportRequestRequired, total=False):
    """Body of ``customers.import_credit_balance``; amounts are decimal strings."""

    external_entity_id: str
    dry_run: bool


class _CreditBalanceImportRequired(TypedDict):
    external_customer_id: str
    external_subscription_id: str
    credit_system_code: str
    granted_amount: str
    remaining_amount: str
    current_period_start: str
    invoice_created: bool
    dry_run: bool
    replayed: bool


class CreditBalanceImport(_CreditBalanceImportRequired, total=False):
    operation_id: str
    balance_source_id: str
    external_entity_id: str
    expires_at: str


class BillingStateImportSubscription(TypedDict):
    plan_code: str
    status: Literal["active"]
    billing_authority: Literal["external"]
    external_reference: str
    current_period_start: str
    current_period_end: str


class _BillingStateImportRequestRequired(TypedDict):
    subscription: BillingStateImportSubscription


class BillingStateImportRequest(_BillingStateImportRequestRequired, total=False):
    """Body of ``customers.import_billing_state`` (the SDK wraps it in ``billing_state_import``)."""

    external_entity_id: str
    dry_run: bool


class _BillingStateImportRequired(TypedDict):
    status: Literal["completed", "validated"]
    dry_run: bool
    external_customer_id: str
    subscription: JSONMapping
    replayed: bool


class BillingStateImport(_BillingStateImportRequired, total=False):
    id: str
    external_entity_id: str
