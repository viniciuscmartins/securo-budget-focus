"""Annual budget planning: planned vs executed per category and month.

Everything here builds on ``budget_service.get_budget_vs_actual`` instead of
re-implementing spending aggregation, so shared splits, FX conversion, the
credit-card accounting mode and transfer exclusion behave exactly as they do
on the Budgets page.
"""

import statistics
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.budget import Budget
from app.models.category import Category
from app.schemas.budget import (
    BudgetSuggestionRow,
    BudgetVsActual,
    BudgetYearApply,
    BudgetYearApplyResult,
    BudgetYearCell,
    BudgetYearPlan,
    BudgetYearRow,
    BudgetYearSuggestion,
)
from app.services.budget_service import _build_budget_map, get_budget_vs_actual
from app.services.category_defaults import DEFAULT_GROUPS

Method = Literal["median", "average", "seasonal"]

ZERO = Decimal("0")
# Weight given to the same calendar month of the history window when
# method == "seasonal"; the rest comes from the flat baseline.
SEASONAL_WEIGHT = Decimal("0.5")


class PlanError(ValueError):
    """Raised for requests the plan endpoints can't honour (maps to HTTP 400)."""


class PlanConflict(RuntimeError):
    """Another save created the same budget first (maps to HTTP 409)."""


def _month_starts(year: int) -> list[date]:
    return [date(year, m, 1) for m in range(1, 13)]


def _shift_month(d: date, delta: int) -> date:
    index = d.year * 12 + (d.month - 1) + delta
    return date(index // 12, index % 12 + 1, 1)


def history_months(today: date, lookback: int) -> list[date]:
    """The ``lookback`` complete months before ``today``'s month, oldest first."""
    current = today.replace(day=1)
    return [_shift_month(current, -(lookback - i)) for i in range(lookback)]


def _whole(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def propose_amounts(
    history: list[tuple[date, Decimal]],
    method: Method,
) -> tuple[Decimal, Decimal, list[Decimal]]:
    """Turn one category's monthly history into 12 proposed monthly amounts.

    ``history`` is (month start, net spend) for every month in the window,
    zeros included. Returns (average, median, proposals Jan..Dec).

    Baseline is the median, which shrugs off one-off spikes (a single
    expensive month won't inflate the plan). Intermittent categories, with
    no spending in at least a third of the months, use the average instead:
    their median sits near zero and would budget far less than they really
    cost over a year. ``seasonal`` blends the baseline with what the same
    calendar month cost in the window.
    """
    values = [v for _, v in history]
    average = sum(values, ZERO) / len(values) if values else ZERO
    median = Decimal(str(statistics.median(values))) if values else ZERO
    idle_months = sum(1 for v in values if v <= 0)
    intermittent = idle_months * 3 >= len(values)

    baseline = average if method == "average" or intermittent else median

    by_calendar_month = {d.month: v for d, v in history}
    proposals: list[Decimal] = []
    for month in range(1, 13):
        amount = baseline
        if method == "seasonal" and month in by_calendar_month:
            amount = baseline * (1 - SEASONAL_WEIGHT) + by_calendar_month[month] * SEASONAL_WEIGHT
        proposals.append(max(_whole(amount), ZERO))
    return _whole(average), _whole(median), proposals


async def _monthly_comparisons(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    months: list[date],
) -> list[dict[uuid.UUID, BudgetVsActual]]:
    out = []
    for month in months:
        rows = await get_budget_vs_actual(session, workspace_id, user_id, month)
        out.append({r.category_id: r for r in rows})
    return out


async def get_year_plan(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    year: Optional[int] = None,
) -> BudgetYearPlan:
    year = year or app_today().year
    per_month = await _monthly_comparisons(session, workspace_id, user_id, _month_starts(year))

    info: dict[uuid.UUID, BudgetVsActual] = {}
    planned: dict[uuid.UUID, list[Optional[Decimal]]] = {}
    actual: dict[uuid.UUID, list[Decimal]] = {}
    for idx, rows in enumerate(per_month):
        for cat_id, row in rows.items():
            info.setdefault(cat_id, row)
            planned.setdefault(cat_id, [None] * 12)[idx] = row.budget_amount
            actual.setdefault(cat_id, [ZERO] * 12)[idx] = row.actual_amount

    rows_out: list[BudgetYearRow] = []
    for cat_id, row in info.items():
        p = planned[cat_id]
        a = actual[cat_id]
        rows_out.append(
            BudgetYearRow(
                category_id=cat_id,
                category_name=row.category_name,
                category_icon=row.category_icon,
                category_color=row.category_color,
                group_id=row.group_id,
                group_name=row.group_name,
                cells=[BudgetYearCell(month=i + 1, planned=p[i], actual=a[i]) for i in range(12)],
                planned_total=sum((x for x in p if x is not None), ZERO),
                actual_total=sum(a, ZERO),
            )
        )
    net_by_category = await _monthly_net_by_category(session, workspace_id, user_id, year)
    planned_income = await _planned_income(session, workspace_id, user_id, year)
    budgeted = {cat_id for cat_id, cells in planned.items() if any(x is not None for x in cells)}
    income_ids = await _income_category_ids(
        session, workspace_id, net_by_category, planned_income, budgeted
    )
    # An income category never shows as an expense, even in a month where
    # its debits happen to outweigh its credits.
    rows_out = [r for r in rows_out if r.category_id not in income_ids]
    rows_out.sort(key=lambda r: (r.actual_total, r.planned_total), reverse=True)

    uncategorized = await _own_share_of_uncategorized(
        session, workspace_id, user_id, year, net_by_category.get(UNCATEGORIZED_OUT, [ZERO] * 12)
    )
    uncategorized_income = net_by_category.get(UNCATEGORIZED_IN, [ZERO] * 12)

    planned_by_month = [
        sum((r.cells[i].planned or ZERO for r in rows_out), ZERO) for i in range(12)
    ]
    actual_by_month = [
        sum((r.cells[i].actual for r in rows_out), ZERO) + uncategorized[i] for i in range(12)
    ]
    income_rows = await _income_rows(session, income_ids, net_by_category, planned_income)
    income_planned = [sum((r.cells[i].planned or ZERO for r in income_rows), ZERO) for i in range(12)]
    income_actual = [
        sum((r.cells[i].actual for r in income_rows), ZERO) + uncategorized_income[i]
        for i in range(12)
    ]
    return BudgetYearPlan(
        year=year,
        rows=rows_out,
        planned_by_month=planned_by_month,
        actual_by_month=actual_by_month,
        planned_total=sum(planned_by_month, ZERO),
        actual_total=sum(actual_by_month, ZERO),
        uncategorized_by_month=uncategorized,
        uncategorized_total=sum(uncategorized, ZERO),
        income_rows=income_rows,
        uncategorized_income_by_month=uncategorized_income,
        uncategorized_income_total=sum(uncategorized_income, ZERO),
        income_planned_by_month=income_planned,
        income_actual_by_month=income_actual,
        income_planned_total=sum(income_planned, ZERO),
        income_actual_total=sum(income_actual, ZERO),
    )


# Keys for rows with no category in `_monthly_net_by_category`.
UNCATEGORIZED_OUT = "uncategorized_out"
UNCATEGORIZED_IN = "uncategorized_in"
NetKey = uuid.UUID | str


async def _monthly_net_by_category(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, year: int
) -> dict[NetKey, list[Decimal]]:
    """Debits minus credits per category and month.

    The same rows the Budgets page counts: posted, up to today, not a
    transfer between your accounts, not ignored, not in a transfer-like or
    ignored category, by the credit-card accounting mode's date. Positive
    is money spent, negative is money received. Rows with no category are
    split instead: debits under `UNCATEGORIZED_OUT`, credits under
    `UNCATEGORIZED_IN`, both as positive amounts.
    """
    from sqlalchemy import extract, func

    from app.models.transaction import Transaction
    from app.services._query_filters import (
        counts_as_user_pnl,
        line_category_id,
        line_primary_or_amount,
        reporting_date_col,
        with_category_lines,
    )
    from app.services.admin_service import get_credit_card_accounting_mode

    report_date = reporting_date_col(await get_credit_card_accounting_mode(session))
    month = extract("month", report_date)
    result = await session.execute(
        with_category_lines(
            select(
                line_category_id(),
                month,
                Transaction.type,
                func.sum(line_primary_or_amount()),
            ).select_from(Transaction)
        )
        .where(
            Transaction.workspace_id == workspace_id,
            report_date >= date(year, 1, 1),
            report_date < date(year + 1, 1, 1),
            report_date <= app_today(),
            Transaction.status == "posted",
            Transaction.source != "opening_balance",
            counts_as_user_pnl(line_category_id()),
        )
        .group_by(line_category_id(), month, Transaction.type)
    )
    out: dict[NetKey, list[Decimal]] = {}
    for cat_id, m, kind, total in result.all():
        amount = abs(Decimal(str(total or 0)))
        signed = amount if kind == "debit" else -amount
        if cat_id is None:
            # Without a category there is nothing to net against: money out
            # is spending and money in is income, kept apart.
            key = UNCATEGORIZED_OUT if kind == "debit" else UNCATEGORIZED_IN
            out.setdefault(key, [ZERO] * 12)[int(m) - 1] += amount
            continue
        out.setdefault(uuid.UUID(str(cat_id)), [ZERO] * 12)[int(m) - 1] += signed
    return out


async def _own_share_of_uncategorized(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    year: int,
    spent: list[Decimal],
) -> list[Decimal]:
    """Uncategorized spending as the Budgets page would count it: without
    the part of your own expenses that other people owe you, and with your
    part of expenses someone else paid in a shared group."""
    from app.core.config import get_settings
    from app.models.user import User
    from app.services._query_filters import (
        owner_split_offset_by_category,
        viewer_shared_spending_by_category,
    )
    from app.services.admin_service import get_credit_card_accounting_mode

    user = await session.get(User, user_id)
    primary = user.primary_currency if user else get_settings().default_currency
    accrual = await get_credit_card_accounting_mode(session) == "accrual"
    today = app_today()
    out = list(spent)
    starts = _month_starts(year)
    for i, start in enumerate(starts):
        if start > today:
            break
        end = starts[i + 1] if i < 11 else date(year + 1, 1, 1)
        owed = await owner_split_offset_by_category(
            session, user_id, start, end, use_effective_date=accrual,
            primary_currency=primary, workspace_id=workspace_id,
        )
        shared = await viewer_shared_spending_by_category(
            session, user_id, start, end, use_effective_date=accrual, primary_currency=primary,
        )
        value = out[i] - Decimal(str(owed.get(None, 0))) + Decimal(str(shared.get(None, 0)))
        out[i] = max(value, ZERO)
    return out


async def _planned_income(
    session: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID, year: int
) -> dict[uuid.UUID, list[Decimal]]:
    """Expected income per category and month, from active recurring credits.

    Every occurrence from the item's start date counts, past months included,
    so the plan for January still shows the salary that was due then. The
    item's current amount is used (with "follow the average" on, that is the
    average of its last payments). A split item counts each part in its own
    category; an item without a category is left out.
    """
    from app.models.recurring_transaction import RecurringTransaction
    from app.models.user import User
    from app.core.config import get_settings
    from app.services.fx_rate_service import convert
    from app.services.recurring_transaction_service import get_occurrences_in_range

    user = await session.get(User, user_id)
    primary = user.primary_currency if user else get_settings().default_currency
    items = (
        await session.execute(
            select(RecurringTransaction).where(
                RecurringTransaction.workspace_id == workspace_id,
                RecurringTransaction.type == "credit",
                RecurringTransaction.is_active == True,  # noqa: E712
            )
        )
    ).scalars().all()
    out: dict[uuid.UUID, list[Decimal]] = {}
    for rec in items:
        occurrences = get_occurrences_in_range(
            start=rec.start_date,
            frequency=rec.frequency,
            end_date=rec.end_date,
            range_start=date(year, 1, 1),
            range_end=date(year + 1, 1, 1),
            intended_day=rec.day_of_month or rec.start_date.day,
            weekend_adjustment=rec.weekend_adjustment,
        )
        if not occurrences:
            continue
        amount, _ = await convert(session, Decimal(str(rec.amount)), rec.currency, primary)
        lines = list(rec.category_lines) if rec.has_category_lines else []
        if lines:
            whole = sum((Decimal(str(line.amount)) for line in lines), ZERO) or Decimal("1")
            parts = [(line.category_id, amount * Decimal(str(line.amount)) / whole) for line in lines]
        elif rec.category_id is not None:
            parts = [(rec.category_id, amount)]
        else:
            continue
        for occurrence in occurrences:
            for cat_id, part in parts:
                out.setdefault(cat_id, [ZERO] * 12)[occurrence.month - 1] += part
    return {k: [v.quantize(Decimal("0.01")) for v in vals] for k, vals in out.items()}


# Names of the default income group in every language Securo seeds
# ("Renda", "Income", ...). A renamed group is still caught by its credits.
_INCOME_GROUP_NAMES = {
    name.strip().casefold() for name in DEFAULT_GROUPS["income"]["names"].values()
}


async def _income_category_ids(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    net_by_category: dict[NetKey, list[Decimal]],
    planned_income: dict[uuid.UUID, list[Decimal]],
    budgeted: set[uuid.UUID],
) -> set[uuid.UUID]:
    """Categories that hold income: in the default income group, or with
    more money coming in than going out over the year, or expected from a
    recurring credit. Transfer-like and ignored categories never count.

    A category with a budget this year stays a spending category when the
    only sign of income is its balance: a large refund must not hide a
    budget the user set."""
    from app.models.category_group import CategoryGroup

    rows = (
        await session.execute(
            select(Category.id, Category.treat_as_transfer, Category.is_ignored, CategoryGroup.name)
            .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
            .where(Category.workspace_id == workspace_id)
        )
    ).all()
    ids: set[uuid.UUID] = set()
    for cat_id, is_transfer, is_ignored, group_name in rows:
        if is_transfer or is_ignored:
            continue
        in_income_group = (group_name or "").strip().casefold() in _INCOME_GROUP_NAMES
        net = sum(net_by_category.get(cat_id, []), ZERO)
        received_more = net < 0 and cat_id not in budgeted
        if in_income_group or received_more or cat_id in planned_income:
            ids.add(cat_id)
    return ids


async def _income_rows(
    session: AsyncSession,
    income_ids: set[uuid.UUID],
    net_by_category: dict[NetKey, list[Decimal]],
    planned_income: dict[uuid.UUID, list[Decimal]],
) -> list[BudgetYearRow]:
    """One row per income category with something planned or received."""
    from app.models.category_group import CategoryGroup

    if not income_ids:
        return []
    cats = (
        await session.execute(
            select(Category, CategoryGroup)
            .outerjoin(CategoryGroup, Category.group_id == CategoryGroup.id)
            .where(Category.id.in_(income_ids))
        )
    ).all()
    rows: list[BudgetYearRow] = []
    for category, group in cats:
        actual = [-x for x in net_by_category.get(category.id, [ZERO] * 12)]
        planned = planned_income.get(category.id)
        if not any(actual) and not planned:
            continue
        cells = [
            BudgetYearCell(month=i + 1, planned=planned[i] if planned and planned[i] else None, actual=actual[i])
            for i in range(12)
        ]
        rows.append(
            BudgetYearRow(
                category_id=category.id,
                category_name=category.name,
                category_icon=category.icon,
                category_color=category.color,
                group_id=group.id if group else None,
                group_name=group.name if group else None,
                cells=cells,
                planned_total=sum((c.planned or ZERO for c in cells), ZERO),
                actual_total=sum(actual, ZERO),
            )
        )
    rows.sort(key=lambda r: (r.actual_total, r.planned_total), reverse=True)
    return rows


async def suggest_year_budget(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    year: Optional[int] = None,
    lookback_months: int = 12,
    method: Method = "median",
) -> BudgetYearSuggestion:
    """Propose a monthly budget per category for ``year`` from recent history.

    Nothing is written; the caller reviews the proposal and applies it via
    ``apply_year_plan``.
    """
    year = year or app_today().year
    window = history_months(app_today(), lookback_months)
    history_cmp = await _monthly_comparisons(session, workspace_id, user_id, window)

    info: dict[uuid.UUID, BudgetVsActual] = {}
    for rows in history_cmp:
        for cat_id, row in rows.items():
            info.setdefault(cat_id, row)

    # Budgets already in effect, so the UI can show what a proposal replaces.
    existing_maps = [await _build_budget_map(session, workspace_id, m) for m in _month_starts(year)]

    rows_out: list[BudgetSuggestionRow] = []
    for cat_id, row in info.items():
        history = [
            (month, cmp[cat_id].actual_amount if cat_id in cmp else ZERO)
            for month, cmp in zip(window, history_cmp)
        ]
        if not any(v > 0 for _, v in history):
            continue  # only a budget or a projection, never spent: nothing to learn from
        average, median, proposed = propose_amounts(history, method)
        existing: list[Optional[Decimal]] = []
        for m in existing_maps:
            entry = m.get(str(cat_id))
            existing.append(entry[0] if entry else None)
        rows_out.append(
            BudgetSuggestionRow(
                category_id=cat_id,
                category_name=row.category_name,
                category_icon=row.category_icon,
                category_color=row.category_color,
                group_id=row.group_id,
                group_name=row.group_name,
                history_average=average,
                history_median=median,
                proposed=proposed,
                existing=existing,
            )
        )
    rows_out.sort(key=lambda r: r.history_average, reverse=True)

    return BudgetYearSuggestion(
        year=year,
        method=method,
        lookback_months=lookback_months,
        history_start=window[0],
        history_end=window[-1],
        rows=rows_out,
    )


async def apply_year_plan(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    data: BudgetYearApply,
) -> BudgetYearApplyResult:
    """Upsert month-specific budgets in bulk.

    Writes non-recurring rows, which take priority over any recurring default
    for that month. ``amount=None`` deletes the month-specific row. If a cell
    appears twice the last one wins.
    """
    wanted: dict[tuple[uuid.UUID, date], Optional[Decimal]] = {}
    for cell in data.cells:
        wanted[(cell.category_id, date(data.year, cell.month, 1))] = cell.amount
    if not wanted:
        return BudgetYearApplyResult(saved=0, deleted=0)

    category_ids = {cat_id for cat_id, _ in wanted}
    found = await session.execute(
        select(Category.id).where(
            Category.workspace_id == workspace_id, Category.id.in_(category_ids)
        )
    )
    if category_ids - set(found.scalars().all()):
        raise PlanError("Unknown category in this workspace")

    existing_result = await session.execute(
        select(Budget).where(
            Budget.workspace_id == workspace_id,
            Budget.is_recurring == False,  # noqa: E712
            Budget.category_id.in_(category_ids),
            Budget.month >= date(data.year, 1, 1),
            Budget.month <= date(data.year, 12, 1),
        )
    )
    existing = {(b.category_id, b.month): b for b in existing_result.scalars().all()}

    saved = deleted = 0
    for key, amount in wanted.items():
        current = existing.get(key)
        if amount is None:
            if current is not None:
                await session.delete(current)
                deleted += 1
        elif current is not None:
            current.amount = amount
            saved += 1
        else:
            session.add(
                Budget(
                    user_id=user_id,
                    workspace_id=workspace_id,
                    category_id=key[0],
                    amount=amount,
                    month=key[1],
                    is_recurring=False,
                )
            )
            saved += 1
    try:
        await session.commit()
    except IntegrityError as exc:
        # Two saves raced to create the same (category, month) budget. Nothing
        # from this request was written; the client can reload and retry.
        await session.rollback()
        raise PlanConflict("The budget plan changed while saving; reload and try again") from exc
    return BudgetYearApplyResult(saved=saved, deleted=deleted)
