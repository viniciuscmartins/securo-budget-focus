import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.transaction import Transaction
from app.providers.cdi import parse_sgs_items, set_cdi_provider
from app.services import cdi_yield_service as svc
from app.services.cdi_yield_service import CdiSettings, accrue, calendar_daily_rate, last_accruable_day

R = Decimal("0.00055131")  # 0.055131% a business day, ~14.9% a year
FRI, SAT, SUN, MON = date(2026, 10, 2), date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5)


# ---------------------------------------------------------------------------
# Banco Central parsing
# ---------------------------------------------------------------------------


def test_parse_sgs_items_converts_percent_to_fraction_and_skips_junk():
    rates = parse_sgs_items(
        [
            {"data": "02/10/2026", "valor": "0.055131"},
            {"data": "2026-10-05", "valor": "0,055131"},
            {"data": "not a date", "valor": "1"},
            {"valor": "1"},
            {"data": "06/10/2026", "valor": "-1"},
        ]
    )
    assert rates == {FRI: R, MON: R}


# ---------------------------------------------------------------------------
# Pure accrual
# ---------------------------------------------------------------------------


def test_business_days_first_day_and_compounding():
    posted = accrue(
        start=FRI, end=MON, opening_balance=Decimal("10000"), movements={},
        rates={FRI: R, MON: R}, percent=Decimal("100"), mode="business_days",
    )
    # Weekend: no CDI, no yield.
    assert set(posted) == {FRI, MON}
    assert posted[FRI] == Decimal("5.51")  # 10000 * 0.00055131 = 5.5131
    # Monday earns on Friday's yield too: exact total 11.0292... -> 11.03.
    exact_total = Decimal("10000") * ((1 + R) ** 2 - 1)
    assert posted[FRI] + posted[MON] == exact_total.quantize(Decimal("0.01"))


def test_percentage_of_cdi_scales_the_daily_rate():
    posted = accrue(
        start=FRI, end=FRI, opening_balance=Decimal("10000"), movements={},
        rates={FRI: R}, percent=Decimal("120"), mode="business_days",
    )
    assert posted[FRI] == Decimal("6.62")  # 10000 * 0.00055131 * 1.2 = 6.61572


def test_calendar_days_accrue_over_the_weekend_with_the_same_yearly_result():
    posted = accrue(
        start=FRI, end=MON, opening_balance=Decimal("10000"), movements={},
        rates={FRI: R, MON: R}, percent=Decimal("100"), mode="calendar_days",
    )
    assert set(posted) == {FRI, SAT, SUN, MON}
    # Same yearly result either way: 365 calendar days == 252 business days.
    daily = calendar_daily_rate(R)
    assert ((1 + daily) ** 365 - (1 + R) ** 252).copy_abs() < Decimal("1e-20")
    assert sum(posted.values()) == (Decimal("10000") * ((1 + daily) ** 4 - 1)).quantize(Decimal("0.01"))


def test_calendar_days_before_any_published_rate_earn_nothing():
    posted = accrue(
        start=SAT, end=MON, opening_balance=Decimal("10000"), movements={},
        rates={MON: R}, percent=Decimal("100"), mode="calendar_days",
    )
    assert set(posted) == {MON}


def test_movements_start_earning_the_next_day():
    # Deposit on Friday: Friday earns on the opening balance only.
    posted = accrue(
        start=FRI, end=MON, opening_balance=Decimal("0"), movements={FRI: Decimal("10000")},
        rates={FRI: R, MON: R}, percent=Decimal("100"), mode="business_days",
    )
    assert set(posted) == {MON}
    assert posted[MON] == Decimal("5.51")


def test_withdrawal_reduces_the_base_and_negative_balance_earns_nothing():
    posted = accrue(
        start=FRI, end=MON, opening_balance=Decimal("1000"), movements={FRI: Decimal("-5000")},
        rates={FRI: R, MON: R}, percent=Decimal("100"), mode="business_days",
    )
    assert set(posted) == {FRI}  # Monday's base is negative


def test_rounding_carries_so_tiny_daily_yield_is_not_lost():
    # 5.00 earns ~0.0028 a day: nothing is posted until it adds up to a cent.
    days = [FRI + timedelta(days=i) for i in range(10)]
    posted = accrue(
        start=days[0], end=days[-1], opening_balance=Decimal("5"), movements={},
        rates={d: R for d in days}, percent=Decimal("100"), mode="business_days",
    )
    exact = Decimal("5") * ((1 + R) ** 10 - 1)  # ~0.0276
    assert sum(posted.values()) == exact.quantize(Decimal("0.01"))
    assert all(v >= Decimal("0.01") for v in posted.values())
    assert len(posted) < 10


def test_last_accruable_day():
    today = date(2026, 10, 7)
    rates = {FRI: R, MON: R, date(2026, 10, 6): R, today: R}
    # Business days: the latest published day that is over (never today).
    assert last_accruable_day("business_days", rates, today) == date(2026, 10, 6)
    # Calendar days: yesterday, once any rate is known.
    assert last_accruable_day("calendar_days", rates, today) == date(2026, 10, 6)
    assert last_accruable_day("calendar_days", {}, today) is None


def test_years_before_handles_leap_day():
    assert svc._years_before(date(2028, 2, 29), 10) == date(2018, 2, 28)
    assert svc._years_before(date(2026, 11, 3), 10) == date(2016, 11, 3)


def test_settings_round_trip_and_bad_json():
    s = CdiSettings(percent=Decimal("105.5"), mode="calendar_days", start_date=FRI)
    assert CdiSettings.from_json(s.to_json()) == s
    assert CdiSettings.from_json("not json") is None
    assert CdiSettings.from_json('{"percent": "x"}') is None
    # Unknown mode or a non-positive percentage is corrupt data, not a default.
    assert CdiSettings.from_json('{"percent": "100", "mode": "weekly", "start_date": "2026-10-02"}') is None
    assert CdiSettings.from_json('{"percent": "0", "mode": "business_days", "start_date": "2026-10-02"}') is None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class FakeProvider:
    def __init__(self, rates):
        self.rates = rates
        self.calls = 0

    async def fetch(self, start, end):
        self.calls += 1
        return {d: r for d, r in self.rates.items() if start <= d <= end}


@pytest.fixture
async def cofrinho(session, test_user, test_workspace) -> Account:
    acc = Account(id=uuid.uuid4(), user_id=test_user.id, name="Cofrinho", type="savings",
                  balance=Decimal("0"), currency="BRL")
    session.add(acc)
    await session.commit()
    return acc


async def _add(session, user, account, day, amount, kind="credit", **extra):
    fields = {"status": "posted", **extra}
    tx = Transaction(id=uuid.uuid4(), user_id=user.id, account_id=account.id, description="Deposit",
                     amount=Decimal(amount), currency="BRL", date=day, type=kind, source="manual",
                     created_at=datetime.now(timezone.utc), **fields)
    session.add(tx)
    await session.commit()
    return tx


async def _yields(session, account_id):
    rows = await session.execute(
        select(Transaction).where(Transaction.account_id == account_id, Transaction.source == "cdi_yield")
        .order_by(Transaction.date)
    )
    return rows.scalars().all()


@pytest.mark.asyncio
async def test_run_posts_one_credit_per_day_and_is_idempotent(session, test_user, cofrinho):
    await _add(session, test_user, cofrinho, date(2026, 10, 1), "10000")
    settings = CdiSettings(percent=Decimal("100"), mode="business_days", start_date=FRI)
    rates = {FRI: R, MON: R}

    created = await svc.run_for_account(session, cofrinho, settings, rates, today=date(2026, 10, 6))
    await session.commit()
    assert created == 2
    rows = await _yields(session, cofrinho.id)
    assert [(r.date, r.type, r.description, r.status) for r in rows] == [
        (FRI, "credit", "Rendimento CDI", "posted"),
        (MON, "credit", "Rendimento CDI", "posted"),
    ]
    assert rows[0].amount == Decimal("5.51")

    # Running again posts nothing new.
    assert await svc.run_for_account(session, cofrinho, settings, rates, today=date(2026, 10, 6)) == 0


@pytest.mark.asyncio
async def test_run_continues_from_the_last_posted_day(session, test_user, cofrinho):
    await _add(session, test_user, cofrinho, date(2026, 10, 1), "10000")
    settings = CdiSettings(percent=Decimal("100"), mode="business_days", start_date=FRI)
    await svc.run_for_account(session, cofrinho, settings, {FRI: R}, today=SAT)
    await session.commit()
    assert await svc.run_for_account(session, cofrinho, settings, {FRI: R, MON: R}, today=date(2026, 10, 6)) == 1
    await session.commit()
    assert [r.date for r in await _yields(session, cofrinho.id)] == [FRI, MON]


@pytest.mark.asyncio
async def test_ignored_and_pending_rows_do_not_earn(session, test_user, cofrinho):
    await _add(session, test_user, cofrinho, date(2026, 10, 1), "10000", is_ignored=True)
    await _add(session, test_user, cofrinho, date(2026, 10, 1), "10000", status="pending")
    settings = CdiSettings(percent=Decimal("100"), mode="business_days", start_date=FRI)
    assert await svc.run_for_account(session, cofrinho, settings, {FRI: R}, today=SAT) == 0


@pytest.mark.asyncio
async def test_rebuild_replaces_history_after_a_past_deposit(session, test_user, cofrinho):
    await _add(session, test_user, cofrinho, date(2026, 10, 1), "10000")
    settings = CdiSettings(percent=Decimal("100"), mode="business_days", start_date=FRI)
    rates = {FRI: R, MON: R}
    await svc.run_for_account(session, cofrinho, settings, rates, today=date(2026, 10, 6))
    await session.commit()

    # A deposit dated in the past doubles the base; a normal run leaves the
    # posted past alone, a rebuild recomputes it.
    await _add(session, test_user, cofrinho, date(2026, 10, 1), "10000")
    assert await svc.run_for_account(session, cofrinho, settings, rates, today=date(2026, 10, 6)) == 0
    assert await svc.run_for_account(session, cofrinho, settings, rates, today=date(2026, 10, 6), rebuild=True) == 2
    await session.commit()
    assert (await _yields(session, cofrinho.id))[0].amount == Decimal("11.03")  # 20000 * R


@pytest.mark.asyncio
async def test_run_all_uses_one_fetch_and_cleans_up_deleted_accounts(session, test_user, cofrinho):
    await _add(session, test_user, cofrinho, date(2026, 10, 1), "10000")
    yesterday = app_today() - timedelta(days=1)
    start = yesterday - timedelta(days=3)
    rates = {start + timedelta(days=i): R for i in range(4)}
    provider = FakeProvider(rates)
    good = CdiSettings(percent=Decimal("100"), mode="calendar_days", start_date=start)
    ghost = uuid.uuid4()
    session.add(AppSetting(key=svc.setting_key(cofrinho.id), value=good.to_json()))
    session.add(AppSetting(key=svc.setting_key(ghost), value=good.to_json()))
    await session.commit()

    posted = await svc.run_all(session, provider=provider)
    assert provider.calls == 1
    assert posted == 4
    assert await session.get(AppSetting, svc.setting_key(ghost)) is None


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_rates():
    today = app_today()
    rates = {today - timedelta(days=i): R for i in range(1, 30)}
    provider = FakeProvider(rates)
    set_cdi_provider(provider)
    yield provider
    set_cdi_provider(None)


@pytest.mark.asyncio
async def test_api_setup_backfills_and_reads_back(client, auth_headers, session, test_user, cofrinho, fake_rates):
    start = app_today() - timedelta(days=5)
    await _add(session, test_user, cofrinho, start - timedelta(days=1), "10000")

    r = await client.get(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers)
    assert r.status_code == 200 and r.json()["configured"] is False

    r = await client.put(
        f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers,
        json={"percent": "100", "mode": "calendar_days", "start_date": start.isoformat()},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["posted_days"] == 5  # start .. yesterday
    assert body["settings"]["configured"] is True
    assert body["settings"]["mode"] == "calendar_days"
    assert float(body["settings"]["total_posted"]) > 0

    # Switching mode rebuilds instead of stacking a second set.
    r = await client.put(
        f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers,
        json={"percent": "100", "mode": "business_days", "start_date": start.isoformat()},
    )
    assert r.status_code == 200
    assert len(await _yields(session, cofrinho.id)) == r.json()["posted_days"]


@pytest.mark.asyncio
async def test_api_rejects_synced_accounts_future_start_and_bad_percent(
    client, auth_headers, test_account, cofrinho, fake_rates
):
    ok_body = {"percent": "100", "mode": "business_days", "start_date": app_today().isoformat()}
    r = await client.put(f"/api/accounts/{test_account.id}/cdi-yield", headers=auth_headers, json=ok_body)
    assert r.status_code == 400 and "manual" in r.json()["detail"]

    future = {**ok_body, "start_date": (app_today() + timedelta(days=1)).isoformat()}
    r = await client.put(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers, json=future)
    assert r.status_code == 400

    too_old = {**ok_body, "start_date": (app_today() - timedelta(days=366 * 11)).isoformat()}
    r = await client.put(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers, json=too_old)
    assert r.status_code == 400 and "10 years" in r.json()["detail"]

    for bad in ("0", "-5", "301"):
        r = await client.put(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers,
                             json={**ok_body, "percent": bad})
        assert r.status_code == 422

    r = await client.get(f"/api/accounts/{uuid.uuid4()}/cdi-yield", headers=auth_headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_api_turn_off_keeps_or_deletes_posted_yield(
    client, auth_headers, session, test_user, cofrinho, fake_rates
):
    start = app_today() - timedelta(days=3)
    await _add(session, test_user, cofrinho, start - timedelta(days=1), "10000")
    await client.put(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers,
                     json={"percent": "100", "mode": "calendar_days", "start_date": start.isoformat()})
    kept = len(await _yields(session, cofrinho.id))
    assert kept > 0

    r = await client.delete(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers)
    assert r.status_code == 204
    assert len(await _yields(session, cofrinho.id)) == kept

    await client.put(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers,
                     json={"percent": "100", "mode": "calendar_days", "start_date": start.isoformat()})
    r = await client.delete(f"/api/accounts/{cofrinho.id}/cdi-yield?delete_posted=true", headers=auth_headers)
    assert r.status_code == 204
    assert await _yields(session, cofrinho.id) == []


@pytest.mark.asyncio
async def test_api_rates_outage_changes_nothing(client, auth_headers, cofrinho):
    import httpx

    class Down:
        async def fetch(self, start, end):
            raise httpx.ConnectError("down")

    set_cdi_provider(Down())
    try:
        r = await client.put(
            f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers,
            json={"percent": "100", "mode": "business_days", "start_date": app_today().isoformat()},
        )
        assert r.status_code == 502
        r = await client.get(f"/api/accounts/{cofrinho.id}/cdi-yield", headers=auth_headers)
        assert r.json()["configured"] is False
    finally:
        set_cdi_provider(None)
