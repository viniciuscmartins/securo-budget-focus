"""Month outlook: will the money in my accounts cover this month?

The dashboard's net worth subtracts each card's whole balance, and a bank
often reports that balance with every future installment in it, so the
number goes negative even when this month is covered. This view answers a
narrower question, the one people plan a month with:

    money in your accounts today
  + income still expected this month       (recurring credits)
  - bills still due this month              (recurring debits, pending rows)
  - each card's bill due this month         (on its due date)
  = what is left at the end of the month

Card installments that fall on later bills are reported apart, as money
already committed, and never subtracted from this month.

Definitions
-----------
- **Cash accounts**: open accounts that are not a credit card or a loan
  (checking, savings, a manual piggy bank...). Today's balance is the same
  posted-only balance the dashboard uses.
- **Expected movements**: recurring items not yet paid, from tomorrow to the
  end of the month, on cash accounts; plus pending and future-dated rows on
  those accounts (a connected account's own pending rows dated today or
  earlier are already in the bank's balance, as on the dashboard). Recurring
  items in a transfer-like category are left out: they move money between
  your own accounts, or pay a card bill already counted below.
- **Card bills**: for every credit card, each bill whose due date falls in the
  window. The amount is the bank's own bill when the bank sends bills;
  otherwise the card's charges and refunds assigned to that due date (Securo
  stores each card row's bill due date as `effective_date`), plus recurring
  charges on the card projected onto that bill. Card payments already made
  toward the bill are subtracted, so paying early does not count twice.
- **Future months**: asking for a later month walks every expected movement
  from tomorrow, so the month starts with what the months before it leave.
"""

import calendar
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, Optional

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.core.config import get_settings
from app.models.account import Account
from app.models.category import Category
from app.models.credit_card_bill import CreditCardBill
from app.models.transaction import Transaction
from app.models.user import User
from app.services.credit_card_service import compute_effective_date
from app.services.fx_rate_service import convert

ZERO = Decimal("0")
CENT = Decimal("0.01")
LIABILITY_TYPES = {"credit_card", "loan"}
# How far before its due date a card payment may arrive and still pay that
# bill (a bill closes roughly a week to ten days before it is due).
PAYMENT_LOOKBACK_DAYS = 20
# How many months ahead a plan may look.
MAX_MONTHS_AHEAD = 12

Kind = Literal["income", "bill", "card_bill", "pending"]


class MonthOutlookError(ValueError):
    """A request the endpoint can't honour (HTTP 400)."""


@dataclass
class OutlookEvent:
    date: date
    kind: Kind
    description: str
    amount: Decimal  # signed, primary currency: + comes in, - goes out
    account_id: Optional[uuid.UUID] = None
    account_name: Optional[str] = None


@dataclass
class CardBill:
    account_id: uuid.UUID
    account_name: str
    due_date: date
    amount: Decimal  # still to pay, primary currency, >= 0
    total: Decimal  # the whole bill, primary currency
    paid: Decimal  # paid toward it already, primary currency
    source: Literal["bank", "computed"]


@dataclass
class MonthOutlook:
    month: date
    currency: str
    today: date
    cash_now: Decimal
    start_balance: Decimal  # cash at the start of the month (today, for this month)
    income_expected: Decimal
    bills_expected: Decimal
    card_bills: Decimal
    end_balance: Decimal
    lowest_balance: Decimal
    lowest_date: date
    committed_later: Decimal  # card installments on bills after this month
    events: list[OutlookEvent] = field(default_factory=list)
    card_bill_details: list[CardBill] = field(default_factory=list)
    cash_accounts: list[tuple[uuid.UUID, str, Decimal]] = field(default_factory=list)


def month_bounds(month: date) -> tuple[date, date]:
    """First and last day of the month."""
    first = month.replace(day=1)
    return first, first.replace(day=calendar.monthrange(first.year, first.month)[1])


def summarize(
    month: date,
    today: date,
    cash_now: Decimal,
    events: list[OutlookEvent],
    currency: str,
    committed_later: Decimal = ZERO,
) -> MonthOutlook:
    """Walk the expected movements from today to the end of `month`.

    Pure: every amount is already signed and in the primary currency.
    Movements before the month (for a later month) only set where it starts.
    """
    first, last = month_bounds(month)
    events = sorted(
        (e for e in events if today < e.date <= last), key=lambda e: (e.date, -e.amount)
    )
    before = [e for e in events if e.date < first]
    inside = [e for e in events if e.date >= first]

    start = cash_now + sum((e.amount for e in before), ZERO)
    start_day = max(first, today)
    balance = start
    lowest, lowest_day = start, start_day
    for event in inside:
        balance += event.amount
        if balance < lowest:
            lowest, lowest_day = balance, event.date

    def total(kind: str, sign: int) -> Decimal:
        return sum((e.amount * sign for e in inside if e.kind == kind), ZERO)

    pending_in = sum((e.amount for e in inside if e.kind == "pending" and e.amount > 0), ZERO)
    pending_out = -sum((e.amount for e in inside if e.kind == "pending" and e.amount < 0), ZERO)
    return MonthOutlook(
        month=first,
        currency=currency,
        today=today,
        cash_now=cash_now,
        start_balance=start,
        income_expected=total("income", 1) + pending_in,
        bills_expected=total("bill", -1) + pending_out,
        card_bills=total("card_bill", -1),
        end_balance=balance,
        lowest_balance=lowest,
        lowest_date=lowest_day,
        committed_later=committed_later,
        events=inside,
    )


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class _Converter:
    """Convert to the primary currency, one rate lookup per currency."""

    def __init__(self, session: AsyncSession, primary: str):
        self.session = session
        self.primary = primary
        self._rates: dict[str, Decimal] = {}

    async def __call__(self, amount: Decimal, currency: Optional[str]) -> Decimal:
        amount = Decimal(str(amount or 0))
        if not currency or currency == self.primary or not amount:
            return amount.quantize(CENT)
        if currency not in self._rates:
            _, rate = await convert(self.session, Decimal("1"), currency, self.primary)
            self._rates[currency] = Decimal(str(rate))
        return (amount * self._rates[currency]).quantize(CENT)


def _name(account: Account) -> str:
    return account.display_name or account.name


async def _open_accounts(session: AsyncSession, workspace_id: uuid.UUID) -> list[Account]:
    from app.services.dashboard_service import _get_open_accounts

    return await _get_open_accounts(session, workspace_id)


async def _cash_balances(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    accounts: list[Account],
    today: date,
    to_primary: _Converter,
) -> list[tuple[uuid.UUID, str, Decimal]]:
    from app.services.dashboard_service import _total_balance_by_currency

    rows = []
    for account in accounts:
        by_currency = await _total_balance_by_currency(session, workspace_id, today, [account.id])
        amount = ZERO
        for currency, value in by_currency.items():
            amount += await to_primary(Decimal(str(value)), currency)
        rows.append((account.id, _name(account), amount))
    return rows


async def _recurring_events(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    start: date,
    end: date,
    by_id: dict[uuid.UUID, Account],
    to_primary: _Converter,
) -> tuple[list[OutlookEvent], list[dict]]:
    """Recurring items not yet paid, as cash events, plus card charges apart."""
    from app.services.dashboard_service import _get_recurring_projections

    projections = await _get_recurring_projections(
        session, workspace_id, start, end + timedelta(days=1)
    )
    events: list[OutlookEvent] = []
    card_charges: list[dict] = []
    for proj in projections:
        account = by_id.get(proj["account_id"])
        if account is None or account.type == "loan":
            continue
        label = proj.get("description") or ""
        amount = await to_primary(Decimal(str(proj["amount"])), proj["currency"])
        if account.type == "credit_card":
            card_charges.append({**proj, "amount_primary": amount, "description": label})
            continue
        signed = amount if proj["type"] == "credit" else -amount
        events.append(
            OutlookEvent(
                date=proj["date"],
                kind="income" if proj["type"] == "credit" else "bill",
                description=label,
                amount=signed,
                account_id=account.id,
                account_name=_name(account),
            )
        )
    return events, card_charges


async def _pending_events(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    end: date,
    today: date,
    cash: dict[uuid.UUID, Account],
    to_primary: _Converter,
) -> list[OutlookEvent]:
    """Pending and future-dated rows on cash accounts (scheduled, not yet in the balance)."""
    from app.services.dashboard_service import _get_forecast_transactions

    rows = await _get_forecast_transactions(
        session, workspace_id, date.min, end + timedelta(days=1), list(cash)
    )
    events = []
    for tx in rows:
        if tx.is_ignored or (
            tx.category and (tx.category.is_ignored or tx.category.treat_as_transfer)
        ):
            continue
        if tx.transfer_pair_id is not None:
            continue  # a move between your own accounts
        account = cash.get(tx.account_id)
        if account is None:
            continue
        if (
            account.connection_id
            and tx.status == "pending"
            and tx.date <= today
            and tx.source != "recurring"
        ):
            continue  # already in the bank's balance
        amount = await to_primary(Decimal(str(tx.amount)), tx.currency)
        events.append(
            OutlookEvent(
                # A stale pending row is still expected; place it tomorrow.
                date=max(tx.date, today + timedelta(days=1)),
                kind="pending",
                description=tx.description,
                amount=amount if tx.type == "credit" else -amount,
                account_id=account.id,
                account_name=_name(account),
            )
        )
    return events


def _counts_on_bill():
    return and_(
        Transaction.is_ignored == False,  # noqa: E712
        Transaction.source != "opening_balance",
        Transaction.transfer_pair_id.is_(None),
        or_(
            Transaction.category_id.is_(None),
            and_(Category.is_ignored == False, Category.treat_as_transfer == False),  # noqa: E712
        ),
    )


async def _card_bills(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    cards: list[Account],
    today: date,
    end: date,
    card_charges: list[dict],
    to_primary: _Converter,
) -> tuple[list[CardBill], Decimal]:
    """Bills due from tomorrow (today included) to `end`, and installments after it."""
    bills: list[CardBill] = []
    committed_later = ZERO
    for card in cards:
        due_dates: set[date] = set()
        bank_bills = {
            b.due_date: b
            for b in (
                await session.execute(
                    select(CreditCardBill).where(
                        CreditCardBill.account_id == card.id,
                        CreditCardBill.due_date >= today,
                        CreditCardBill.due_date <= end,
                    )
                )
            ).scalars()
        }
        due_dates.update(bank_bills)

        rows = (
            (
                await session.execute(
                    select(Transaction)
                    .outerjoin(Category, Transaction.category_id == Category.id)
                    .where(
                        Transaction.account_id == card.id,
                        Transaction.effective_date >= today,
                        _counts_on_bill(),
                    )
                )
            )
            .scalars()
            .all()
        )
        computed: dict[date, Decimal] = {}
        for tx in rows:
            amount = await to_primary(Decimal(str(tx.amount)), tx.currency)
            signed = amount if tx.type == "debit" else -amount
            if tx.effective_date > end:
                if tx.type == "debit":
                    committed_later += amount
                continue
            computed[tx.effective_date] = computed.get(tx.effective_date, ZERO) + signed
        for charge in card_charges:
            if charge["account_id"] != card.id:
                continue
            due = compute_effective_date(
                charge["date"], card.statement_close_day, card.payment_due_day
            )
            if today <= due <= end:
                signed = (
                    charge["amount_primary"]
                    if charge["type"] == "debit"
                    else -charge["amount_primary"]
                )
                computed[due] = computed.get(due, ZERO) + signed
        due_dates.update(computed)

        for due in sorted(due_dates):
            if due in bank_bills:
                bill = bank_bills[due]
                total = await to_primary(bill.total_amount, bill.currency or card.currency)
                # The bank's bill doesn't know about recurring charges still to come.
                source: Literal["bank", "computed"] = "bank"
            else:
                total = computed.get(due, ZERO)
                source = "computed"
            if total <= 0:
                continue
            paid = await _paid_toward(session, card, due, today, to_primary)
            bills.append(
                CardBill(
                    account_id=card.id,
                    account_name=_name(card),
                    due_date=due,
                    amount=max(total - paid, ZERO),
                    total=total,
                    paid=min(paid, total),
                    source=source,
                )
            )
    return bills, committed_later


async def _paid_toward(
    session: AsyncSession, card: Account, due: date, today: date, to_primary: _Converter
) -> Decimal:
    """Card payments that already arrived for the bill due on `due`."""
    rows = (
        (
            await session.execute(
                select(Transaction)
                .outerjoin(Category, Transaction.category_id == Category.id)
                .where(
                    Transaction.account_id == card.id,
                    Transaction.type == "credit",
                    Transaction.is_ignored == False,  # noqa: E712
                    Transaction.date >= due - timedelta(days=PAYMENT_LOOKBACK_DAYS),
                    Transaction.date <= min(due, today),
                    or_(
                        Transaction.transfer_pair_id.isnot(None), Category.treat_as_transfer == True
                    ),  # noqa: E712
                )
            )
        )
        .scalars()
        .all()
    )
    total = ZERO
    for tx in rows:
        total += await to_primary(Decimal(str(tx.amount)), tx.currency)
    return total


async def get_outlook(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    month: Optional[date] = None,
) -> MonthOutlook:
    today = app_today()
    month = (month or today).replace(day=1)
    if month < today.replace(day=1):
        raise MonthOutlookError("The outlook covers this month and the months ahead")
    months_ahead = (month.year - today.year) * 12 + month.month - today.month
    if months_ahead > MAX_MONTHS_AHEAD:
        raise MonthOutlookError(f"The outlook looks at most {MAX_MONTHS_AHEAD} months ahead")
    _, end = month_bounds(month)

    user = await session.get(User, user_id)
    primary = user.primary_currency if user else get_settings().default_currency
    to_primary = _Converter(session, primary)

    accounts = await _open_accounts(session, workspace_id)
    by_id = {a.id: a for a in accounts}
    cash = {a.id: a for a in accounts if a.type not in LIABILITY_TYPES}
    cards = [a for a in accounts if a.type == "credit_card"]

    cash_rows = await _cash_balances(session, workspace_id, list(cash.values()), today, to_primary)
    cash_now = sum((amount for _, _, amount in cash_rows), ZERO)

    events, card_charges = await _recurring_events(
        session, workspace_id, today + timedelta(days=1), end, by_id, to_primary
    )
    events += await _pending_events(session, workspace_id, end, today, cash, to_primary)
    bills, committed_later = await _card_bills(
        session, workspace_id, cards, today, end, card_charges, to_primary
    )
    for bill in bills:
        if bill.amount <= 0:
            continue
        events.append(
            OutlookEvent(
                # A bill due today has not left the account yet.
                date=max(bill.due_date, today + timedelta(days=1)),
                kind="card_bill",
                description=bill.account_name,
                amount=-bill.amount,
                account_id=bill.account_id,
                account_name=bill.account_name,
            )
        )

    outlook = summarize(month, today, cash_now, events, primary, committed_later)
    first, _ = month_bounds(month)
    outlook.card_bill_details = [b for b in bills if b.due_date >= first]
    outlook.cash_accounts = cash_rows
    return outlook
