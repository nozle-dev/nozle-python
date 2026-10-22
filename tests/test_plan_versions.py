from __future__ import annotations

import pytest
import requests_mock

from nozle import Nozle, NozleAPIError, NozleAuthenticationError, NozleValidationError

CORE = "https://api.example/core/api/v1/plans"
MIGRATION = {
    "nozle_id": "mig_1",
    "plan_code": "pro",
    "from_version": 1,
    "to_version": 2,
    "status": "running",
    "matched_count": 3,
    "succeeded_count": 0,
    "skipped_count": 0,
    "failed_count": 0,
    "created_at": "2026-10-22T10:00:00Z",
}


def client(api_key: str = "sk_test") -> Nozle:
    return Nozle(
        api_key, base_url="https://api.example/engine", events_url="https://api.example/core"
    )


def test_plans_carry_their_version(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.get(
        "https://api.example/engine/api/v1/plans",
        json={
            "plans": [
                {
                    "code": "pro",
                    "name": "Pro",
                    "amount_cents": 5900,
                    "amount_currency": "USD",
                    "interval": "monthly",
                    "version": 2,
                    "is_current": True,
                }
            ]
        },
    )
    plan = client().plans()[0]
    assert (plan["version"], plan["is_current"]) == (2, True)


def test_lists_and_reads_versions_from_core(requests_mock: requests_mock.Mocker) -> None:
    versions = [
        {
            "nozle_id": "p1",
            "version": 1,
            "is_current": False,
            "created_at": "2026-10-01T09:00:00Z",
            "amount_cents": 4900,
            "amount_currency": "USD",
            "active_subscriptions_count": 42,
        },
        {
            "nozle_id": "p2",
            "version": 2,
            "is_current": True,
            "created_at": "2026-10-15T09:00:00Z",
            "amount_cents": 5900,
            "amount_currency": "USD",
            "active_subscriptions_count": 7,
        },
    ]
    requests_mock.get(f"{CORE}/pro%20plan/versions", json={"versions": versions})
    requests_mock.get(
        f"{CORE}/pro?version=1", json={"plan": {"code": "pro", "version": 1, "is_current": False}}
    )
    nozle = client()
    assert nozle.plan_versions.list("pro plan") == versions
    assert nozle.plan_versions.get("pro", 1)["version"] == 1
    assert requests_mock.last_request.qs == {"version": ["1"]}


def test_previews_starts_lists_reads_and_cancels_a_migration(
    requests_mock: requests_mock.Mocker,
) -> None:
    base = f"{CORE}/pro/version-migrations"
    preview = {
        "plan_code": "pro",
        "from_version": 1,
        "to_version": 2,
        "items": [
            {
                "external_subscription_id": "sub_1",
                "external_customer_id": "cus_1",
                "outcome": "move",
                "skip_reason": None,
                "effective_on": "2026-11-01",
                "current_amount_cents": 4900,
                "new_amount_cents": 5900,
            }
        ],
    }
    requests_mock.post(f"{base}/preview", json={"plan_version_migration_preview": preview})
    requests_mock.post(base, json={"plan_version_migration": MIGRATION})
    requests_mock.get(base, json={"plan_version_migrations": [MIGRATION]})
    detail = {
        **MIGRATION,
        "items": [
            {"external_subscription_id": "sub_1", "status": "succeeded", "move_status": "pending"}
        ],
    }
    requests_mock.get(f"{base}/mig_1", json={"plan_version_migration": detail})
    requests_mock.post(
        f"{base}/mig_1/cancel", json={"plan_version_migration": {**MIGRATION, "status": "canceled"}}
    )
    nozle = client()

    assert nozle.plan_versions.preview_migration("pro", 1) == preview
    assert requests_mock.last_request.json() == {"from_version": 1}
    assert nozle.plan_versions.create_migration("pro", 1, to_version=2)["nozle_id"] == "mig_1"
    assert requests_mock.last_request.json() == {"from_version": 1, "to_version": 2}
    assert nozle.plan_versions.list_migrations("pro") == [MIGRATION]
    assert nozle.plan_versions.get_migration("pro", "mig_1")["items"][0]["move_status"] == "pending"
    assert nozle.plan_versions.cancel_migration("pro", "mig_1")["status"] == "canceled"
    assert [(r.method, r.path) for r in requests_mock.request_history] == [
        ("POST", "/core/api/v1/plans/pro/version-migrations/preview"),
        ("POST", "/core/api/v1/plans/pro/version-migrations"),
        ("GET", "/core/api/v1/plans/pro/version-migrations"),
        ("GET", "/core/api/v1/plans/pro/version-migrations/mig_1"),
        ("POST", "/core/api/v1/plans/pro/version-migrations/mig_1/cancel"),
    ]


def test_a_running_migration_raises_a_409(requests_mock: requests_mock.Mocker) -> None:
    requests_mock.post(
        f"{CORE}/pro/version-migrations",
        status_code=409,
        json={"status": 409, "error": "Conflict", "code": "plan_version_migration_running"},
    )
    with pytest.raises(NozleAPIError) as error:
        client().plan_versions.create_migration("pro", 1)
    assert error.value.status_code == 409
    assert error.value.code == "plan_version_migration_running"


@pytest.mark.parametrize(
    "call",
    [
        lambda n: n.plan_versions.preview_migration("pro", 0),
        lambda n: n.plan_versions.create_migration("pro", 1, to_version=0),
        lambda n: n.plan_versions.create_migration("pro", True),
        lambda n: n.plan_versions.get("pro", 0),
        lambda n: n.plan_versions.list(" "),
        lambda n: n.plan_versions.cancel_migration("pro", ""),
        lambda n: n.subscribe("cust", "pro", plan_version=0),
    ],
)
def test_bad_input_is_refused_before_any_request(requests_mock: requests_mock.Mocker, call) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(NozleValidationError):
        call(client())
    assert requests_mock.request_history == []


def test_plan_versions_need_a_secret_key() -> None:
    with pytest.raises(NozleAuthenticationError):
        client("pk_browser").plan_versions.list("pro")


def test_subscribe_sends_a_plan_version_only_when_asked(
    requests_mock: requests_mock.Mocker,
) -> None:
    requests_mock.post(
        "https://api.example/engine/api/v1/subscribe",
        json={"subscription_id": "sub-cust-pro", "status": "active"},
    )
    nozle = client()
    nozle.subscribe("cust", "pro")
    nozle.subscribe("cust", "pro", plan_version=1)
    assert [r.json() for r in requests_mock.request_history] == [
        {"plan_code": "pro", "customer_id": "cust"},
        {"plan_code": "pro", "customer_id": "cust", "plan_version": 1},
    ]
