"""Access-token scopes for the fork's own routes.

Upstream's scope map is deny by default, so a route it does not list is
closed to every token. Each fork route sits under the resource a person
would expect, and stays closed to the wrong one.
"""
import pytest
from httpx import AsyncClient

from app.core.access_token_scopes import required_scope


@pytest.mark.parametrize(
    "method,path,scope",
    [
        # Annual budget plan: under /api/budgets already.
        ("GET", "/api/budgets/year-plan", "budgets:read"),
        ("PUT", "/api/budgets/year-plan", "budgets:write"),
        # CDI yield: an account setting.
        ("GET", "/api/accounts/abc/cdi-yield", "accounts:read"),
        ("PUT", "/api/accounts/abc/cdi-yield", "accounts:write"),
        # Recurring items made from transactions, and their amount mode.
        ("GET", "/api/recurring-links/amount-modes", "recurring:read"),
        ("POST", "/api/recurring-links/from-transaction/abc", "recurring:write"),
        ("PUT", "/api/recurring-links/abc/amount-mode", "recurring:write"),
        # The month outlook: a read-only report.
        ("GET", "/api/month-outlook", "reports:read"),
    ],
)
def test_fork_routes_map_to_the_expected_scope(method, path, scope):
    assert required_scope(method, path) == scope


def test_the_month_outlook_cannot_be_written_through_a_token():
    assert required_scope("POST", "/api/month-outlook") is None


async def _token(client: AsyncClient, headers: dict, scopes: list[str]) -> dict:
    resp = await client.post(
        "/api/access-tokens", headers=headers, json={"name": "script", "scopes": scopes}
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest.mark.asyncio
async def test_a_reports_token_reads_the_month_outlook(client: AsyncClient, auth_headers: dict):
    bearer = await _token(client, auth_headers, ["reports:read"])
    assert (await client.get("/api/month-outlook", headers=bearer)).status_code == 200
    # Not a recurring scope.
    resp = await client.get("/api/recurring-links/amount-modes", headers=bearer)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_a_recurring_token_reads_amount_modes_but_a_read_one_cannot_write(
    client: AsyncClient, auth_headers: dict
):
    bearer = await _token(client, auth_headers, ["recurring:read"])
    assert (await client.get("/api/recurring-links/amount-modes", headers=bearer)).status_code == 200
    resp = await client.put(
        "/api/recurring-links/00000000-0000-0000-0000-000000000000/amount-mode",
        headers=bearer,
        json={"follow_average": False},
    )
    assert resp.status_code == 403
