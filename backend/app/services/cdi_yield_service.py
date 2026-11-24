"""CDI yield for manual savings accounts (e.g. a bank "piggy bank").

An account can be set to earn a percentage of the CDI rate. Each day the
yield accrues, a "Rendimento CDI" credit is posted to the account, so its
balance follows what the bank shows without anyone typing it in.

Settings live in `app_settings` under `cdi_yield:<account id>` as JSON, so
the feature needs no migration of its own.

How the yield is computed
-------------------------
- Base: the account's balance at the end of the previous day, computed like
  Securo's own account balance (posted, not ignored, not in an ignored
  category), including the yield already posted. Movements of a day start
  earning the next day. A zero or negative balance earns nothing.
- Daily rate: Banco Central's CDI for that day (SGS series 12, % per business
  day) times the account's percentage of CDI, the usual "x% do CDI" rule.
- Mode "business_days": yield only on days with a published CDI rate.
  Mode "calendar_days": yield every calendar day, using the latest published
  rate converted to a calendar-day rate with the same yearly result:
  (1 + daily)^252 = (1 + calendar_daily)^365.
- Rounding: amounts are kept exact while compounding and rounded to cents
  only when posted, carrying the remainder forward so the posted total never
  drifts from the exact total by more than a cent.
- Income tax: by default the yield is gross. With `tax_percent` set (e.g.
  22.5), each day's yield is posted net of that rate, the way banks that
  credit "rendimento líquido" show it, so the balance matches the bank's.
  The net amount is what compounds, as it is what stays in the account.
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Literal, Optional

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.category import Category
from app.models.transaction import Transaction
from app.providers.cdi import CdiRateProvider, get_cdi_provider

logger = logging.getLogger(__name__)

Mode = Literal["business_days", "calendar_days"]

SETTING_PREFIX = "cdi_yield:"
YIELD_SOURCE = "cdi_yield"
YIELD_DESCRIPTION = "Rendimento CDI"
ZERO = Decimal("0")
CENT = Decimal("0.01")
# Calendar-day conversion: 252 business days and 365 calendar days a year.
_BUSINESS_DAYS_PER_YEAR = 252
_CALENDAR_DAYS_PER_YEAR = 365
# Enough digits that daily compounding over years stays exact to the cent.
# Set per calculation, never globally: the rest of the app keeps its context.
_PRECISION = 34
# How far back a start date may go. Bounds the work one save does (every day
# from the start date is computed and posted) and keeps the rate history to
# one Banco Central request range.
MAX_HISTORY_YEARS = 10


class CdiYieldError(ValueError):
    """A request the CDI yield endpoints can't honour (HTTP 400)."""


class CdiAccountNotFound(CdiYieldError):
    """No such account in this workspace (HTTP 404)."""


@dataclass(frozen=True)
class CdiSettings:
    percent: Decimal  # 100 = 100% of CDI
    mode: Mode
    start_date: date
    enabled: bool = True
    tax_percent: Decimal = Decimal("0")  # income tax withheld from each day's yield

    def to_json(self) -> str:
        return json.dumps(
            {
                "enabled": self.enabled,
                "percent": str(self.percent),
                "mode": self.mode,
                "start_date": self.start_date.isoformat(),
                "tax_percent": str(self.tax_percent),
            }
        )

    @staticmethod
    def from_json(raw: str) -> Optional["CdiSettings"]:
        try:
            data = json.loads(raw)
            settings = CdiSettings(
                enabled=bool(data.get("enabled", True)),
                percent=Decimal(str(data["percent"])),
                mode=data["mode"],
                start_date=date.fromisoformat(data["start_date"]),
                tax_percent=Decimal(str(data.get("tax_percent", "0"))),
            )
        except (ValueError, KeyError, TypeError, ArithmeticError):
            return None
        if settings.mode not in ("business_days", "calendar_days") or settings.percent <= 0:
            return None
        if not (0 <= settings.tax_percent < 100):
            return None
        return settings


def _years_before(day: date, years: int) -> date:
    """Same calendar date `years` earlier; 29 Feb falls back to 28 Feb."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)


def setting_key(account_id: uuid.UUID) -> str:
    return f"{SETTING_PREFIX}{account_id}"


# ---------------------------------------------------------------------------
# Pure calculation
# ---------------------------------------------------------------------------


def calendar_daily_rate(business_daily: Decimal) -> Decimal:
    """Calendar-day rate with the same yearly result as `business_daily`."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        yearly = (1 + business_daily) ** _BUSINESS_DAYS_PER_YEAR
        return yearly ** (Decimal(1) / _CALENDAR_DAYS_PER_YEAR) - 1


def accrue(
    *,
    start: date,
    end: date,
    opening_balance: Decimal,
    movements: dict[date, Decimal],
    rates: dict[date, Decimal],
    percent: Decimal,
    mode: Mode,
    tax_percent: Decimal = ZERO,
) -> dict[date, Decimal]:
    """Yield to post per day for days in [start, end], rounded to cents.

    `opening_balance` is the balance at the end of the day before `start`;
    `movements` holds each later day's net change (deposits positive,
    withdrawals negative) excluding yield. `rates` are daily CDI rates as
    fractions keyed by business day. With `tax_percent`, each day's yield
    is reduced by that income tax rate before it is posted and compounded. Days earning less than half a cent are
    left out; their yield carries into the next posted day.
    """
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return _accrue(start, end, opening_balance, movements, rates, percent, mode, tax_percent)


def _accrue(start, end, opening_balance, movements, rates, percent, mode, tax_percent=ZERO) -> dict[date, Decimal]:
    factor = percent / Decimal(100) * (1 - tax_percent / Decimal(100))
    business_days = sorted(rates)
    posted: dict[date, Decimal] = {}
    balance = opening_balance  # exact, compounding
    exact_total = ZERO
    posted_total = ZERO
    latest_rate: Optional[Decimal] = None
    rate_idx = 0
    calendar_cache: dict[Decimal, Decimal] = {}

    day = start
    while day <= end:
        # Latest published rate on or before `day`, for calendar mode.
        while rate_idx < len(business_days) and business_days[rate_idx] <= day:
            latest_rate = rates[business_days[rate_idx]]
            rate_idx += 1

        if mode == "business_days":
            daily = rates.get(day)
        else:
            if latest_rate is None:
                daily = None
            else:
                if latest_rate not in calendar_cache:
                    calendar_cache[latest_rate] = calendar_daily_rate(latest_rate)
                daily = calendar_cache[latest_rate]

        if daily is not None and balance > 0:
            earned = balance * daily * factor
            exact_total += earned
            balance += earned
            to_post = exact_total.quantize(CENT, rounding=ROUND_HALF_EVEN) - posted_total
            if to_post > 0:
                posted[day] = to_post
                posted_total += to_post

        balance += movements.get(day, ZERO)
        day += timedelta(days=1)
    return posted


def last_accruable_day(mode: Mode, rates: dict[date, Decimal], today: date) -> Optional[date]:
    """The last day whose yield can be posted now.

    Business days: the latest day with a published rate, never today or later
    (the day must be over). Calendar days: yesterday, once any rate is known.
    """
    yesterday = today - timedelta(days=1)
    if mode == "business_days":
        known = [d for d in rates if d <= yesterday]
        return max(known) if known else None
    return yesterday if rates else None


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


async def get_settings(session: AsyncSession, account_id: uuid.UUID) -> Optional[CdiSettings]:
    row = await session.get(AppSetting, setting_key(account_id))
    return CdiSettings.from_json(row.value) if row else None


async def eligible_account(
    session: AsyncSession, workspace_id: uuid.UUID, account_id: uuid.UUID
) -> Account:
    """The account, if CDI yield can be set on it: in this workspace, manual,
    open, and holding money (not a card or loan)."""
    account = await session.get(Account, account_id)
    if account is None or account.workspace_id != workspace_id:
        raise CdiAccountNotFound("Account not found")
    if account.connection_id is not None:
        raise CdiYieldError("CDI yield is only for manual accounts; a synced account gets its yield from the bank")
    if account.type in ("credit_card", "loan"):
        raise CdiYieldError("CDI yield is only for accounts that hold money")
    if account.is_closed:
        raise CdiYieldError("Account is closed")
    return account


async def _balance_inputs(
    session: AsyncSession, account: Account, start: date
) -> tuple[Decimal, dict[date, Decimal]]:
    """Opening balance before `start` and each later day's net movement,
    counted the way Securo counts an account's balance, without yield rows.

    Every amount is in the account's own currency: a row in another currency
    is converted at its date's rate. `amount_primary` is the user's primary
    currency, which is not the account's when, say, a BRL piggy bank belongs
    to someone who reports in USD, so it is only a last resort.
    """
    from app.services.fx_rate_service import convert_or_none

    effective = func.coalesce(Transaction.amount_primary, Transaction.amount)
    amount = Transaction.amount
    rates: dict[tuple[str, date], Optional[Decimal]] = {}
    rows = await session.execute(
        select(Transaction.date, Transaction.type, Transaction.currency, amount, effective)
        .outerjoin(Category, Transaction.category_id == Category.id)
        .where(
            Transaction.account_id == account.id,
            Transaction.status == "posted",
            Transaction.is_ignored == False,  # noqa: E712
            Transaction.source != YIELD_SOURCE,
            or_(Transaction.category_id.is_(None), Category.is_ignored == False),  # noqa: E712
        )
    )
    opening = ZERO
    movements: dict[date, Decimal] = {}
    for tx_date, tx_type, currency, raw_amount, converted in rows.all():
        if not currency or currency == account.currency:
            value = Decimal(str(raw_amount))
        else:
            key = (currency, tx_date)
            if key not in rates:
                # Stored rates only (the daily FX job keeps them), with the
                # closest day as fallback: no network call per movement.
                _, rates[key] = await convert_or_none(
                    session, Decimal("1"), currency, account.currency, tx_date, allow_fetch=False
                )
            rate = rates[key]
            if rate is not None:
                value = Decimal(str(raw_amount)) * rate
            else:
                logger.warning(
                    "No %s->%s rate for %s; using the primary-currency amount",
                    currency,
                    account.currency,
                    tx_date,
                )
                value = Decimal(str(converted))
        signed = value if tx_type == "credit" else -value
        if tx_date < start:
            opening += signed
        else:
            movements[tx_date] = movements.get(tx_date, ZERO) + signed
    return opening, movements


# Fields a person may have changed on a posted yield row, kept across a rebuild.
_KEPT_FIELDS = ("category_id", "payee", "payee_id", "notes", "is_ignored")


async def _delete_yield_rows(session: AsyncSession, account_id: uuid.UUID) -> dict[date, dict]:
    """Delete the account's posted yield, with the other leg of any transfer
    a rule made from it (it would be left pointing at nothing).

    Returns, per day, the fields a person set on that day's row, so a rebuild
    can put them back.
    """
    rows = (
        (
            await session.execute(
                select(Transaction).where(
                    Transaction.account_id == account_id, Transaction.source == YIELD_SOURCE
                )
            )
        )
        .scalars()
        .all()
    )
    edits: dict[date, dict] = {}
    for tx in rows:
        kept = {f: getattr(tx, f) for f in _KEPT_FIELDS if getattr(tx, f) not in (None, False)}
        if kept:
            edits[tx.date] = kept
    pair_ids = {tx.transfer_pair_id for tx in rows if tx.transfer_pair_id is not None}
    # The rows go in bulk, which skips the ORM's cleanup: remove the files of
    # anything attached to them (or to their transfer legs) first.
    from app.services.attachment_service import cleanup_attachment_files

    doomed = [tx.id for tx in rows]
    if pair_ids:
        doomed += (
            await session.execute(
                select(Transaction.id).where(
                    Transaction.transfer_pair_id.in_(pair_ids),
                    Transaction.source != YIELD_SOURCE,
                )
            )
        ).scalars().all()
    await cleanup_attachment_files(session, doomed)
    if pair_ids:
        await session.execute(
            delete(Transaction).where(
                Transaction.transfer_pair_id.in_(pair_ids),
                Transaction.source != YIELD_SOURCE,
            )
        )
    await session.execute(
        delete(Transaction).where(
            Transaction.account_id == account_id, Transaction.source == YIELD_SOURCE
        )
    )
    return edits


async def _existing_yield_days(session: AsyncSession, account_id: uuid.UUID) -> set[date]:
    rows = await session.execute(
        select(Transaction.date).where(
            Transaction.account_id == account_id, Transaction.source == YIELD_SOURCE
        )
    )
    return set(rows.scalars().all())


async def _post_yield(session: AsyncSession, account: Account, day: date, amount: Decimal) -> Transaction:
    from app.services.rule_service import apply_rules_to_transaction
    from app.services.fx_rate_service import stamp_primary_amount

    tx = Transaction(
        user_id=account.user_id,
        workspace_id=account.workspace_id,
        account_id=account.id,
        description=YIELD_DESCRIPTION,
        amount=amount,
        currency=account.currency,
        date=day,
        type="credit",
        source=YIELD_SOURCE,
        status="posted",
    )
    session.add(tx)
    await session.flush()
    await stamp_primary_amount(session, account.user_id, tx)
    # Lets a rule categorize "Rendimento CDI" like any other transaction.
    await apply_rules_to_transaction(session, account.user_id, tx)
    return tx


async def run_for_account(
    session: AsyncSession,
    account: Account,
    settings: CdiSettings,
    rates: dict[date, Decimal],
    *,
    today: Optional[date] = None,
    rebuild: bool = False,
) -> int:
    """Post the yield days that are due and not posted yet. Returns how many.

    With `rebuild`, every posted yield row of the account is deleted first and
    the whole history is recomputed (used after a settings change, or when
    past movements were added or changed). Does not commit.
    """
    if not settings.enabled:
        return 0
    # Serialize runs per account: the scheduled job and a settings save could
    # otherwise both post the same day. A no-op on SQLite (tests).
    await session.execute(select(Account.id).where(Account.id == account.id).with_for_update())
    today = today or app_today()
    end = last_accruable_day(settings.mode, rates, today)
    if end is None or end < settings.start_date:
        return 0

    edits: dict[date, dict] = {}
    if rebuild:
        edits = await _delete_yield_rows(session, account.id)
        existing: set[date] = set()
    else:
        existing = await _existing_yield_days(session, account.id)

    opening, movements = await _balance_inputs(session, account, settings.start_date)
    schedule = accrue(
        start=settings.start_date,
        end=end,
        opening_balance=opening,
        movements=movements,
        rates=rates,
        percent=settings.percent,
        mode=settings.mode,
        tax_percent=settings.tax_percent,
    )
    # Only days after the last posted one: the past is not rewritten unless
    # asked to (rebuild), so editing an old movement never silently changes
    # yield the user has already seen.
    last_posted = max(existing) if existing else None
    created = 0
    for day, amount in sorted(schedule.items()):
        if day in existing or (last_posted is not None and day <= last_posted):
            continue
        tx = await _post_yield(session, account, day, amount)
        # What the user set on the old row of that day wins over the rules.
        for field_name, value in edits.get(day, {}).items():
            setattr(tx, field_name, value)
        created += 1
    return created


async def save_settings(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    settings: CdiSettings,
    provider: Optional[CdiRateProvider] = None,
) -> int:
    """Store the settings and rebuild the account's yield from the start date.
    Returns how many yield days were posted. Commits."""
    account = await eligible_account(session, workspace_id, account_id)
    today = app_today()
    if settings.start_date > today:
        raise CdiYieldError("The start date can't be in the future")
    if settings.start_date < _years_before(today, MAX_HISTORY_YEARS):
        raise CdiYieldError(f"The start date can be at most {MAX_HISTORY_YEARS} years ago")
    row = await session.get(AppSetting, setting_key(account_id))
    if row is None:
        session.add(AppSetting(key=setting_key(account_id), value=settings.to_json()))
    else:
        row.value = settings.to_json()

    created = 0
    if settings.enabled:
        rates = await (provider or get_cdi_provider()).fetch(settings.start_date - timedelta(days=10), app_today())
        created = await run_for_account(session, account, settings, rates, rebuild=True)
    await session.commit()
    return created


async def recalculate(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    provider: Optional[CdiRateProvider] = None,
) -> int:
    """Recompute the whole yield history (after past movements changed)."""
    account = await eligible_account(session, workspace_id, account_id)
    settings = await get_settings(session, account_id)
    if settings is None:
        raise CdiYieldError("CDI yield is not set up for this account")
    rates = await (provider or get_cdi_provider()).fetch(settings.start_date - timedelta(days=10), app_today())
    created = await run_for_account(session, account, settings, rates, rebuild=True)
    await session.commit()
    return created


async def remove_settings(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    account_id: uuid.UUID,
    delete_posted: bool,
) -> None:
    """Turn CDI yield off. Posted yield stays unless `delete_posted`. Commits."""
    await eligible_account(session, workspace_id, account_id)
    row = await session.get(AppSetting, setting_key(account_id))
    if row is not None:
        await session.delete(row)
    if delete_posted:
        await _delete_yield_rows(session, account_id)
    await session.commit()


async def summary(session: AsyncSession, account_id: uuid.UUID) -> tuple[Optional[date], Decimal]:
    """Last posted yield day and the total posted so far."""
    row = (
        await session.execute(
            select(func.max(Transaction.date), func.coalesce(func.sum(Transaction.amount), 0)).where(
                and_(Transaction.account_id == account_id, Transaction.source == YIELD_SOURCE)
            )
        )
    ).one()
    return row[0], Decimal(str(row[1]))


async def run_all(session: AsyncSession, provider: Optional[CdiRateProvider] = None) -> int:
    """Daily job: post due yield for every account that has CDI yield on.

    One rate fetch covers all accounts. Settings for accounts that no longer
    exist are cleaned up. Each account commits on its own, so one failure
    doesn't hold back the others.
    """
    rows = (
        await session.execute(select(AppSetting).where(AppSetting.key.startswith(SETTING_PREFIX)))
    ).scalars().all()
    jobs: list[tuple[uuid.UUID, CdiSettings]] = []
    for row in rows:
        settings = CdiSettings.from_json(row.value)
        try:
            account_id = uuid.UUID(row.key[len(SETTING_PREFIX):])
        except ValueError:
            continue
        if settings is not None and settings.enabled:
            jobs.append((account_id, settings))
    if not jobs:
        return 0

    earliest = min(s.start_date for _, s in jobs)
    rates = await (provider or get_cdi_provider()).fetch(earliest - timedelta(days=10), app_today())

    total = 0
    for account_id, settings in jobs:
        account = await session.get(Account, account_id)
        if account is None:
            stale = await session.get(AppSetting, setting_key(account_id))
            if stale is not None:
                await session.delete(stale)
            await session.commit()
            continue
        if account.is_closed or account.connection_id is not None:
            continue
        try:
            total += await run_for_account(session, account, settings, rates)
            await session.commit()
        except Exception:  # noqa: BLE001 - keep the other accounts going
            await session.rollback()
            logger.exception("CDI yield failed for account %s", account_id)
    return total
