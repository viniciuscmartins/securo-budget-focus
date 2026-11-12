"""Month outlook: money now + income expected - bills - card bills due this month."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.models.account import Account
from app.models.credit_card_bill import CreditCardBill
from app.models.recurring_transaction import RecurringTransaction
from app.models.transaction import Transaction
from app.services import month_outlook_service as svc
from app.services.credit_card_service import compute_effective_date
from app.services.month_outlook_service import OutlookEvent, summarize

TODAY = date(2026, 11, 6)
D = Decimal


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    for target in (
        "app.services.month_outlook_service.app_today",
        "app.services.dashboard_service.app_today",
    ):
        monkeypatch.setattr(target, lambda: TODAY)


async def _account(session, user, name, kind, **extra) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        name=name,
        type=kind,
        balance=D("0"),
        currency="BRL",
        **extra,
    )
    session.add(acc)
    await session.commit()
    return acc


async def _tx(
    session, user, account, amount, day, kind="debit", description="x", **extra
) -> Transaction:
    effective = day
    if account.type == "credit_card":
        effective = compute_effective_date(
            day, account.statement_close_day, account.payment_due_day
        )
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        account_id=account.id,
        description=description,
        amount=D(amount),
        currency="BRL",
        date=day,
        effective_date=effective,
        type=kind,
        source="manual",
        status="posted",
        created_at=datetime.now(timezone.utc),
        **extra,
    )
    session.add(tx)
    await session.commit()
    return tx


async def _recurring(session, user, workspace, account, description, amount, kind, next_occurrence):
    rec = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description=description,
        amount=D(amount),
        currency="BRL",
        type=kind,
        frequency="monthly",
        day_of_month=next_occurrence.day,
        start_date=date(2026, 1, next_occurrence.day),
        next_occurrence=next_occurrence,
        auto_generate=False,
        is_active=True,
    )
    session.add(rec)
    await session.commit()
    return rec


@pytest.fixture
async def household(session, test_user, test_workspace):
    checking = await _account(session, test_user, "Checking", "checking")
    savings = await _account(session, test_user, "Savings", "savings")
    card = await _account(
        session, test_user, "Card", "credit_card", statement_close_day=1, payment_due_day=10
    )
    await _tx(session, test_user, checking, "3000", date(2026, 11, 1), kind="credit")
    await _tx(session, test_user, savings, "1000", date(2026, 10, 1), kind="credit")
    # October purchase: closes Nov 1, due Nov 10.
    await _tx(session, test_user, card, "800", date(2026, 10, 20), description="Groceries")
    # An installment on a later bill (due Jan 10): committed, not this month.
    await _tx(session, test_user, card, "200", date(2026, 12, 20), description="TV 3/10")
    await _recurring(
        session, test_user, test_workspace, checking, "Salary", "5000", "credit", date(2026, 11, 25)
    )
    await _recurring(
        session, test_user, test_workspace, checking, "School", "1200", "debit", date(2026, 11, 15)
    )
    # On the card: each charge lands on the next bill.
    await _recurring(
        session, test_user, test_workspace, card, "Streaming", "50", "debit", date(2026, 11, 12)
    )
    return checking, savings, card


# ---------------------------------------------------------------------------
# pure walk
# ---------------------------------------------------------------------------


def test_summarize_walks_the_month_and_finds_the_lowest_point():
    events = [
        OutlookEvent(date(2026, 11, 25), "income", "Salary", D("5000")),
        OutlookEvent(date(2026, 11, 10), "card_bill", "Card", D("-800")),
        OutlookEvent(date(2026, 11, 15), "bill", "School", D("-1200")),
        OutlookEvent(date(2026, 12, 15), "bill", "Next month", D("-1")),  # outside
    ]
    out = summarize(date(2026, 11, 1), TODAY, D("1500"), events, "BRL", D("200"))
    assert (out.start_balance, out.end_balance) == (D("1500"), D("4500"))
    assert (out.income_expected, out.bills_expected, out.card_bills) == (
        D("5000"),
        D("1200"),
        D("800"),
    )
    assert (out.lowest_balance, out.lowest_date) == (D("-500"), date(2026, 11, 15))
    assert [e.description for e in out.events] == ["Card", "School", "Salary"]
    assert out.committed_later == D("200")


def test_summarize_a_later_month_starts_from_what_earlier_months_leave():
    events = [
        OutlookEvent(date(2026, 11, 25), "income", "Salary", D("5000")),
        OutlookEvent(date(2026, 12, 15), "bill", "School", D("-1200")),
    ]
    out = summarize(date(2026, 12, 1), TODAY, D("100"), events, "BRL")
    assert out.start_balance == D("5100")
    assert out.end_balance == D("3900")
    assert out.lowest_date == date(2026, 12, 15)


# ---------------------------------------------------------------------------
# from the database
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_this_month(session, test_user, test_workspace, household):
    out = await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 11, 1))

    assert out.cash_now == D("4000")  # checking + savings; the card is not cash
    assert out.income_expected == D("5000")
    assert out.bills_expected == D("1200")
    assert out.card_bills == D("800")  # only the bill due Nov 10
    assert out.end_balance == D("7000")
    assert (out.lowest_balance, out.lowest_date) == (D("2000"), date(2026, 11, 15))
    assert out.committed_later == D("200")
    [bill] = out.card_bill_details
    assert (bill.due_date, bill.amount, bill.source) == (date(2026, 11, 10), D("800"), "computed")
    assert [(e.date, e.kind, e.description) for e in out.events] == [
        (date(2026, 11, 10), "card_bill", "Card"),
        (date(2026, 11, 15), "bill", "School"),
        (date(2026, 11, 25), "income", "Salary"),
    ]


@pytest.mark.asyncio
async def test_next_month_carries_over_and_puts_card_recurring_on_its_bill(
    session, test_user, test_workspace, household
):
    out = await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 12, 1))

    assert out.start_balance == D("7000")
    # Nov 12 streaming charge -> bill due Dec 10.
    [bill] = out.card_bill_details
    assert (bill.due_date, bill.amount) == (date(2026, 12, 10), D("50"))
    assert out.end_balance == D("7000") - D("50") - D("1200") + D("5000")


@pytest.mark.asyncio
async def test_a_bill_already_paid_is_not_counted_twice(
    session, test_user, test_workspace, household
):
    checking, _, card = household
    pair = uuid.uuid4()
    await _tx(
        session,
        test_user,
        checking,
        "800",
        date(2026, 11, 5),
        description="Card payment",
        transfer_pair_id=pair,
    )
    await _tx(
        session,
        test_user,
        card,
        "800",
        date(2026, 11, 5),
        kind="credit",
        description="Payment",
        transfer_pair_id=pair,
    )

    out = await svc.get_outlook(session, test_workspace.id, test_user.id)

    assert out.cash_now == D("3200")
    assert out.card_bills == D("0")
    [bill] = out.card_bill_details
    assert (bill.total, bill.paid, bill.amount) == (D("800"), D("800"), D("0"))
    assert out.end_balance == D("7000")  # same end as before paying


@pytest.mark.asyncio
async def test_the_banks_own_bill_wins(session, test_user, test_workspace, household):
    _, _, card = household
    session.add(
        CreditCardBill(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=card.id,
            external_id="b1",
            due_date=date(2026, 11, 10),
            total_amount=D("950"),
            currency="BRL",
        )
    )
    await session.commit()

    out = await svc.get_outlook(session, test_workspace.id, test_user.id)
    [bill] = out.card_bill_details
    assert (bill.amount, bill.source) == (D("950"), "bank")


@pytest.mark.asyncio
async def test_pending_and_ignored_rows(session, test_user, test_workspace, household):
    checking, _, _ = household
    await _tx(session, test_user, checking, "300", date(2026, 11, 20), description="Scheduled rent")
    await _tx(session, test_user, checking, "999", date(2026, 11, 21), is_ignored=True)

    out = await svc.get_outlook(session, test_workspace.id, test_user.id)
    pending = [e for e in out.events if e.kind == "pending"]
    assert [(e.description, e.amount) for e in pending] == [("Scheduled rent", D("-300"))]
    assert out.bills_expected == D("1500")


@pytest.mark.asyncio
async def test_rejects_past_and_far_months(session, test_user, test_workspace):
    with pytest.raises(svc.MonthOutlookError):
        await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 10, 1))
    with pytest.raises(svc.MonthOutlookError):
        await svc.get_outlook(session, test_workspace.id, test_user.id, date(2028, 1, 1))


@pytest.mark.asyncio
async def test_api(client, auth_headers, household):
    r = await client.get("/api/month-outlook", headers=auth_headers, params={"month": "2026-11-01"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert D(body["end_balance"]) == D("7000")
    assert {a["account_name"] for a in body["cash_accounts"]} == {"Checking", "Savings"}
    r = await client.get("/api/month-outlook", headers=auth_headers, params={"month": "2020-01-01"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_unpaid_bills_due_today_or_earlier_this_month_are_still_owed(
    session, test_user, test_workspace
):
    checking = await _account(session, test_user, "Checking", "checking")
    await _tx(session, test_user, checking, "1000", date(2026, 11, 1), kind="credit")
    await _recurring(session, test_user, test_workspace, checking, "Rent", "300", "debit", TODAY)
    await _recurring(
        session, test_user, test_workspace, checking, "Gym", "100", "debit", date(2026, 11, 3)
    )
    # Left behind since September: only this month's occurrence (Nov 2) is
    # owed; September's and October's are not carried over.
    await _recurring(
        session, test_user, test_workspace, checking, "Stale", "50", "debit", date(2026, 9, 2)
    )

    out = await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 11, 1))

    assert out.bills_expected == D("450")
    assert out.end_balance == D("550")
    tomorrow = date(2026, 11, 7)
    assert sorted((e.date, e.description, e.overdue) for e in out.events) == [
        (tomorrow, "Gym", True),
        (tomorrow, "Rent", True),
        (tomorrow, "Stale", True),
    ]


@pytest.mark.asyncio
async def test_an_unpaid_card_charge_lands_on_the_bill_it_will_post_to(
    session, test_user, test_workspace
):
    checking = await _account(session, test_user, "Checking", "checking")
    card = await _account(
        session, test_user, "Card", "credit_card", statement_close_day=5, payment_due_day=15
    )
    await _tx(session, test_user, checking, "1000", date(2026, 11, 1), kind="credit")
    # Due Nov 4 but not charged yet: it posts from tomorrow, after the
    # Nov 5 close, so it goes on the bill due Dec 15 (not the one due Nov 15),
    # together with the Dec 4 charge.
    await _recurring(
        session, test_user, test_workspace, card, "Streaming", "50", "debit", date(2026, 11, 4)
    )

    out = await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 11, 1))
    assert out.card_bill_details == []

    out = await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 12, 1))
    [bill] = out.card_bill_details
    assert (bill.due_date, bill.amount) == (date(2026, 12, 15), D("100"))


@pytest.mark.asyncio
async def test_the_banks_bill_gets_the_recurring_charges_still_to_come(
    session, test_user, test_workspace, household
):
    _, _, card = household
    # The bank's open bill due Dec 10 knows nothing of the Nov 12 streaming charge.
    session.add(
        CreditCardBill(
            user_id=test_user.id,
            workspace_id=test_workspace.id,
            account_id=card.id,
            external_id="open-dec",
            due_date=date(2026, 12, 10),
            total_amount=D("300"),
            currency="BRL",
        )
    )
    await session.commit()

    out = await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 12, 1))

    [bill] = out.card_bill_details
    assert (bill.due_date, bill.amount, bill.source) == (date(2026, 12, 10), D("350"), "bank")


@pytest.mark.asyncio
async def test_investment_accounts_are_not_cash(session, test_user, test_workspace, household):
    broker = await _account(session, test_user, "Broker", "investment")
    await _tx(session, test_user, broker, "50000", date(2026, 10, 1), kind="credit")
    await _recurring(
        session, test_user, test_workspace, broker, "Dividends", "100", "credit", date(2026, 11, 20)
    )

    out = await svc.get_outlook(session, test_workspace.id, test_user.id, date(2026, 11, 1))

    assert out.cash_now == D("4000")
    assert out.income_expected == D("5000")


@pytest.mark.asyncio
async def test_a_collection_counts_only_its_accounts(
    session, test_user, test_workspace, household
):
    checking, savings, _ = household
    out = await svc.get_outlook(
        session, test_workspace.id, test_user.id, date(2026, 11, 1), [savings.id]
    )
    assert out.cash_now == D("1000")
    assert out.events == [] and out.card_bill_details == []

    out = await svc.get_outlook(
        session, test_workspace.id, test_user.id, date(2026, 11, 1), [checking.id]
    )
    assert out.cash_now == D("3000")
    assert out.end_balance == D("6800")  # 3000 + 5000 - 1200, no card


@pytest.mark.asyncio
async def test_api_collection_filter(client, auth_headers, household):
    _, savings, _ = household
    r = await client.get(
        "/api/month-outlook",
        headers=auth_headers,
        params={"month": "2026-11-01", "account_ids": [str(savings.id)]},
    )
    assert r.status_code == 200, r.text
    assert D(r.json()["cash_now"]) == D("1000")


# ---------------------------------------------------------------------------
# next bill per card (sidebar)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_next_card_bill_is_the_one_due_next(session, test_user, test_workspace, household):
    _, _, card = household

    bills = await svc.get_next_card_bills(session, test_workspace.id, test_user.id)

    [bill] = bills
    assert (bill.account_id, bill.due_date) == (card.id, date(2026, 11, 10))
    assert (bill.amount, bill.total) == (D("800"), D("800"))


@pytest.mark.asyncio
async def test_a_paid_bill_moves_on_to_the_following_one(
    session, test_user, test_workspace, household
):
    checking, _, card = household
    pair = uuid.uuid4()
    await _tx(session, test_user, checking, "800", date(2026, 11, 5), transfer_pair_id=pair)
    await _tx(
        session, test_user, card, "800", date(2026, 11, 5), kind="credit", transfer_pair_id=pair
    )

    [bill] = await svc.get_next_card_bills(session, test_workspace.id, test_user.id)

    # November's bill is paid; December's holds the streaming charge of Nov 12.
    assert (bill.due_date, bill.amount) == (date(2026, 12, 10), D("50"))


@pytest.mark.asyncio
async def test_next_card_bill_uses_the_banks_bill(session, test_user, test_workspace, household):
    _, _, card = household
    session.add(
        CreditCardBill(
            user_id=test_user.id, workspace_id=test_workspace.id, account_id=card.id,
            external_id="b1", due_date=date(2026, 11, 10), total_amount=D("950"), currency="BRL",
        )
    )
    await session.commit()

    [bill] = await svc.get_next_card_bills(session, test_workspace.id, test_user.id)

    assert (bill.amount, bill.source) == (D("950"), "bank")


@pytest.mark.asyncio
async def test_a_card_with_nothing_to_pay_has_no_next_bill(session, test_user, test_workspace):
    await _account(
        session, test_user, "Empty card", "credit_card", statement_close_day=1, payment_due_day=10
    )

    assert await svc.get_next_card_bills(session, test_workspace.id, test_user.id) == []


@pytest.mark.asyncio
async def test_card_bills_api(client, auth_headers, household):
    _, _, card = household
    r = await client.get("/api/month-outlook/card-bills", headers=auth_headers)
    assert r.status_code == 200, r.text
    [bill] = r.json()
    assert bill["account_id"] == str(card.id)
    assert bill["due_date"] == "2026-11-10"
    assert D(bill["amount"]) == D("800")


@pytest.mark.asyncio
async def test_card_bills_api_needs_a_login(client):
    r = await client.get("/api/month-outlook/card-bills")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_an_unpaid_bill_past_its_due_date_shows_first_and_overdue(
    session, test_user, test_workspace, household, monkeypatch
):
    _, _, card = household
    later = date(2026, 11, 12)  # November's bill (due Nov 10, R$ 800) is unpaid
    for target in (
        "app.services.month_outlook_service.app_today",
        "app.services.dashboard_service.app_today",
    ):
        monkeypatch.setattr(target, lambda: later)

    [bill] = await svc.get_next_card_bills(session, test_workspace.id, test_user.id)

    assert (bill.account_id, bill.due_date, bill.amount) == (card.id, date(2026, 11, 10), D("800"))
    assert bill.overdue is True


@pytest.mark.asyncio
async def test_a_late_payment_settles_the_overdue_bill(
    session, test_user, test_workspace, household, monkeypatch
):
    checking, _, card = household
    later = date(2026, 11, 12)
    for target in (
        "app.services.month_outlook_service.app_today",
        "app.services.dashboard_service.app_today",
    ):
        monkeypatch.setattr(target, lambda: later)
    pair = uuid.uuid4()
    # Paid two days late.
    await _tx(session, test_user, checking, "800", date(2026, 11, 12), transfer_pair_id=pair)
    await _tx(
        session, test_user, card, "800", date(2026, 11, 12), kind="credit", transfer_pair_id=pair
    )

    [bill] = await svc.get_next_card_bills(session, test_workspace.id, test_user.id)

    assert (bill.due_date, bill.amount, bill.overdue) == (date(2026, 12, 10), D("50"), False)


@pytest.mark.asyncio
async def test_a_payment_not_marked_as_transfer_still_settles_the_overdue_bill(
    session, test_user, test_workspace, household, monkeypatch
):
    """A bank-synced card often gets the payment as a plain credit."""
    _, _, card = household
    later = date(2026, 11, 12)
    for target in (
        "app.services.month_outlook_service.app_today",
        "app.services.dashboard_service.app_today",
    ):
        monkeypatch.setattr(target, lambda: later)
    await _tx(session, test_user, card, "800", date(2026, 11, 11), kind="credit",
              description="PAGAMENTO RECEBIDO")

    bills = await svc.get_next_card_bills(session, test_workspace.id, test_user.id)

    assert all(not b.overdue for b in bills)


@pytest.mark.asyncio
async def test_a_partly_paid_overdue_bill_shows_what_is_still_owed(
    session, test_user, test_workspace, household, monkeypatch
):
    _, _, card = household
    later = date(2026, 11, 12)
    for target in (
        "app.services.month_outlook_service.app_today",
        "app.services.dashboard_service.app_today",
    ):
        monkeypatch.setattr(target, lambda: later)
    await _tx(session, test_user, card, "500", date(2026, 11, 11), kind="credit",
              description="PAGAMENTO PARCIAL")

    [bill] = await svc.get_next_card_bills(session, test_workspace.id, test_user.id)

    assert (bill.due_date, bill.amount, bill.overdue) == (date(2026, 11, 10), D("300"), True)
