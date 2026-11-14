import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.budget import Budget
from app.models.transaction import Transaction
from app.schemas.budget import BudgetCreate
from app.schemas.budget_plan import BudgetPlanCellWrite, BudgetYearApply
from app.services.budget_plan_service import (
    PlanConflict,
    PlanError,
    apply_year_plan,
    get_year_plan,
    history_months,
    propose_amounts,
    suggest_year_budget,
)
from app.services.budget_service import create_budget


def _m(year: int, month: int) -> date:
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_history_months_are_complete_months_before_today():
    assert history_months(date(2026, 10, 17), 3) == [
        date(2026, 7, 1),
        date(2026, 8, 1),
        date(2026, 9, 1),
    ]


def test_history_months_cross_year_boundary():
    assert history_months(date(2026, 2, 5), 4) == [
        date(2025, 10, 1),
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
    ]


def test_median_ignores_a_single_spike():
    history = [(_m(2025, m), Decimal("100")) for m in range(1, 12)]
    history.append((_m(2025, 12), Decimal("1200")))
    average, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("100")
    assert average == Decimal("192")  # 2300 / 12 = 191.67, dragged up by the spike
    assert proposed == [Decimal("100")] * 12


def test_average_method_uses_the_mean():
    history = [(_m(2025, m), Decimal("100")) for m in range(1, 12)]
    history.append((_m(2025, 12), Decimal("1200")))
    _, _, proposed = propose_amounts(history, "average")
    assert proposed == [Decimal("192")] * 12


def test_lumpy_category_falls_back_to_average_not_zero():
    # One 1200 payment in twelve months: the median is 0, but the category
    # clearly costs something, so the proposal must not be zero.
    history = [(_m(2025, m), Decimal("0")) for m in range(1, 13)]
    history[3] = (_m(2025, 4), Decimal("1200"))
    _, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("0")
    assert proposed == [Decimal("100")] * 12


def test_intermittent_category_uses_average_even_when_median_is_not_zero():
    # Spent in only 5 of 12 months: the median (45) would budget a quarter of
    # what the category really costs (~146/month).
    amounts = ["0", "0", "350", "0", "120", "0", "980", "0", "210", "90", "0", "0"]
    history = [(_m(2025, i + 1), Decimal(a)) for i, a in enumerate(amounts)]
    average, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("0")
    assert average == Decimal("146")  # 1750 / 12
    assert proposed == [Decimal("146")] * 12


def test_an_occasional_quiet_month_keeps_the_median():
    history = [(_m(2025, 1), Decimal("0"))] + [(_m(2025, m), Decimal("100")) for m in range(2, 13)]
    _, median, proposed = propose_amounts(history, "median")
    assert median == Decimal("100")
    assert proposed == [Decimal("100")] * 12


def test_seasonal_blends_same_month_with_baseline():
    history = [(_m(2025, m), Decimal("100")) for m in range(1, 13)]
    history[11] = (_m(2025, 12), Decimal("300"))  # December is expensive
    _, _, proposed = propose_amounts(history, "seasonal")
    assert proposed[11] == Decimal("200")  # 0.5 * 100 + 0.5 * 300
    assert proposed[0] == Decimal("100")


def test_seasonal_with_short_history_uses_baseline_for_unseen_months():
    history = [(_m(2026, m), Decimal("50")) for m in range(7, 10)]  # Jul-Sep only
    _, _, proposed = propose_amounts(history, "seasonal")
    assert proposed == [Decimal("50")] * 12


# ---------------------------------------------------------------------------
# DB-backed behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
async def account(session: AsyncSession, test_user) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="PlanAcc",
        type="checking",
        balance=Decimal("5000"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    return acc


async def _spend(session, test_user, account, category, on: date, amount: str, kind="debit"):
    session.add(
        Transaction(
            id=uuid.uuid4(),
            user_id=test_user.id,
            account_id=account.id,
            category_id=category.id,
            description="x",
            amount=Decimal(amount),
            date=on,
            type=kind,
            source="manual",
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_year_plan_shows_planned_and_actual_per_month(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=food.id, amount=Decimal("500"), month=_m(2024, 3)),
    )
    await _spend(session, test_user, account, food, date(2024, 3, 10), "120")
    await _spend(session, test_user, account, food, date(2024, 3, 20), "30")
    await _spend(session, test_user, account, food, date(2024, 5, 2), "75")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert plan.year == 2024
    row = next(r for r in plan.rows if r.category_id == food.id)
    assert [c.month for c in row.cells] == list(range(1, 13))
    march, may = row.cells[2], row.cells[4]
    assert march.planned == Decimal("500")
    assert march.actual == Decimal("150")
    assert may.planned is None
    assert may.actual == Decimal("75")
    assert row.actual_total == Decimal("225")
    assert row.planned_total == Decimal("500")
    assert plan.planned_by_month[2] == Decimal("500")
    assert plan.actual_by_month[2] == Decimal("150")
    assert plan.actual_total == Decimal("225")


@pytest.mark.asyncio
async def test_year_plan_refund_nets_against_spend(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    await _spend(session, test_user, account, food, date(2024, 6, 3), "200")
    await _spend(session, test_user, account, food, date(2024, 6, 9), "40", kind="credit")
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)
    row = next(r for r in plan.rows if r.category_id == food.id)
    assert row.cells[5].actual == Decimal("160")


@pytest.mark.asyncio
async def test_year_plan_recurring_budget_applies_from_effective_month(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[1]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=cat.id, amount=Decimal("300"), month=_m(2024, 6), is_recurring=True
        ),
    )
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)
    row = next(r for r in plan.rows if r.category_id == cat.id)
    planned = [c.planned for c in row.cells]
    assert planned[:5] == [None] * 5
    assert planned[5:] == [Decimal("300")] * 7


@pytest.mark.asyncio
async def test_suggestion_is_based_on_history_and_skips_unspent_categories(
    session, test_user, test_workspace, test_categories, account
):
    food, transport, _income = test_categories
    window = history_months(app_today(), 6)
    for month in window:
        await _spend(session, test_user, account, food, month.replace(day=5), "200")
    # A refund in one month nets against that month's spend.
    await _spend(session, test_user, account, food, window[0].replace(day=9), "50", kind="credit")
    # Budget but no spending at all: not enough to learn from.
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=transport.id, amount=Decimal("400"), month=window[-1]),
    )

    suggestion = await suggest_year_budget(
        session,
        test_workspace.id,
        test_user.id,
        year=app_today().year + 1,
        lookback_months=6,
        method="median",
    )

    assert suggestion.history_start == window[0]
    assert suggestion.history_end == window[-1]
    assert {r.category_id for r in suggestion.rows} == {food.id}
    row = suggestion.rows[0]
    assert row.proposed == [Decimal("200")] * 12  # median of 150,200,200,200,200,200
    assert row.history_average == Decimal("192")  # 1150 / 6 = 191.67
    assert row.existing == [None] * 12


@pytest.mark.asyncio
async def test_suggestion_reports_budgets_already_in_effect(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    for month in history_months(app_today(), 3):
        await _spend(session, test_user, account, food, month.replace(day=5), "100")
    year = app_today().year + 1
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(category_id=food.id, amount=Decimal("999"), month=_m(year, 2)),
    )

    suggestion = await suggest_year_budget(
        session, test_workspace.id, test_user.id, year=year, lookback_months=3
    )
    existing = suggestion.rows[0].existing
    assert existing[1] == Decimal("999")
    assert existing[0] is None and existing[2] is None


@pytest.mark.asyncio
async def test_suggestion_does_not_write_anything(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    for month in history_months(app_today(), 3):
        await _spend(session, test_user, account, food, month.replace(day=5), "100")
    await suggest_year_budget(session, test_workspace.id, test_user.id, lookback_months=3)
    assert (await session.execute(select(Budget))).scalars().all() == []


@pytest.mark.asyncio
async def test_apply_creates_updates_and_deletes(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[0]
    result = await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027,
            cells=[
                BudgetPlanCellWrite(category_id=cat.id, month=1, amount=Decimal("100")),
                BudgetPlanCellWrite(category_id=cat.id, month=2, amount=Decimal("200")),
            ],
        ),
    )
    assert (result.saved, result.deleted) == (2, 0)

    result = await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027,
            cells=[
                BudgetPlanCellWrite(category_id=cat.id, month=1, amount=Decimal("150")),  # update
                BudgetPlanCellWrite(category_id=cat.id, month=2, amount=None),  # delete
                BudgetPlanCellWrite(
                    category_id=cat.id, month=2, amount=None
                ),  # duplicate, harmless
                BudgetPlanCellWrite(category_id=cat.id, month=3, amount=None),  # nothing to delete
            ],
        ),
    )
    assert (result.saved, result.deleted) == (1, 1)

    rows = (await session.execute(select(Budget).order_by(Budget.month))).scalars().all()
    assert [(b.month, b.amount, b.is_recurring) for b in rows] == [
        (date(2027, 1, 1), Decimal("150.00"), False)
    ]


@pytest.mark.asyncio
async def test_apply_override_beats_recurring_default_and_clearing_restores_it(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[0]
    await create_budget(
        session,
        test_workspace.id,
        test_user.id,
        BudgetCreate(
            category_id=cat.id, amount=Decimal("300"), month=_m(2027, 1), is_recurring=True
        ),
    )
    await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027,
            cells=[BudgetPlanCellWrite(category_id=cat.id, month=4, amount=Decimal("450"))],
        ),
    )
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2027)
    row = next(r for r in plan.rows if r.category_id == cat.id)
    assert row.cells[3].planned == Decimal("450")
    assert row.cells[4].planned == Decimal("300")

    await apply_year_plan(
        session,
        test_workspace.id,
        test_user.id,
        BudgetYearApply(
            year=2027, cells=[BudgetPlanCellWrite(category_id=cat.id, month=4, amount=None)]
        ),
    )
    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2027)
    row = next(r for r in plan.rows if r.category_id == cat.id)
    assert row.cells[3].planned == Decimal("300")


@pytest.mark.asyncio
async def test_apply_rejects_unknown_category_and_writes_nothing(
    session, test_user, test_workspace, test_categories
):
    cat = test_categories[0]
    with pytest.raises(PlanError):
        await apply_year_plan(
            session,
            test_workspace.id,
            test_user.id,
            BudgetYearApply(
                year=2027,
                cells=[
                    BudgetPlanCellWrite(category_id=cat.id, month=1, amount=Decimal("10")),
                    BudgetPlanCellWrite(category_id=uuid.uuid4(), month=1, amount=Decimal("10")),
                ],
            ),
        )
    assert (await session.execute(select(Budget))).scalars().all() == []


def test_apply_schema_rejects_negative_amounts_and_bad_months():
    with pytest.raises(ValueError):
        BudgetPlanCellWrite(category_id=uuid.uuid4(), month=1, amount=Decimal("-1"))
    with pytest.raises(ValueError):
        BudgetPlanCellWrite(category_id=uuid.uuid4(), month=13, amount=Decimal("1"))


@pytest.mark.asyncio
async def test_apply_reports_a_conflict_when_a_concurrent_save_wins(
    session, test_user, test_workspace, test_categories, monkeypatch
):
    from sqlalchemy.exc import IntegrityError

    async def losing_commit():
        raise IntegrityError(
            "INSERT INTO budgets", {}, Exception("uq_budget_per_category_month_type")
        )

    monkeypatch.setattr(session, "commit", losing_commit)
    with pytest.raises(PlanConflict):
        await apply_year_plan(
            session,
            test_workspace.id,
            test_user.id,
            BudgetYearApply(
                year=2027,
                cells=[
                    BudgetPlanCellWrite(
                        category_id=test_categories[0].id, month=1, amount=Decimal("1")
                    )
                ],
            ),
        )
    monkeypatch.undo()
    assert (await session.execute(select(Budget))).scalars().all() == []


def test_apply_schema_rejects_amounts_the_column_cannot_hold():
    BudgetPlanCellWrite(category_id=uuid.uuid4(), month=1, amount=Decimal("9999999999999.99"))
    with pytest.raises(ValueError):
        BudgetPlanCellWrite(category_id=uuid.uuid4(), month=1, amount=Decimal("10000000000000"))


# ---------------------------------------------------------------------------
# Income, uncategorized spending and the net
# ---------------------------------------------------------------------------


async def _row(session, test_user, account, category, on: date, amount: str, kind="debit"):
    session.add(
        Transaction(
            id=uuid.uuid4(), user_id=test_user.id, account_id=account.id,
            category_id=category.id if category else None, description="x",
            amount=Decimal(amount), date=on, type=kind, source="manual",
            created_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_spending_without_a_category_is_counted(
    session, test_user, test_workspace, test_categories, account
):
    food = test_categories[0]
    await _row(session, test_user, account, food, date(2024, 3, 10), "100")
    await _row(session, test_user, account, None, date(2024, 3, 12), "40")
    await _row(session, test_user, account, None, date(2024, 3, 15), "15", kind="credit")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    # Money in without a category is income, not a refund of the spending.
    assert plan.uncategorized_by_month[2] == Decimal("40")
    assert plan.uncategorized_total == Decimal("40")
    assert plan.actual_by_month[2] == Decimal("140")  # food 100 + uncategorized 40
    assert plan.uncategorized_income_by_month[2] == Decimal("15")
    assert plan.income_actual_by_month[2] == Decimal("15")
    assert all(r.category_id != food.id or r.actual_total == 100 for r in plan.rows)


@pytest.mark.asyncio
async def test_income_shows_actual_credits_and_never_counts_as_spending(
    session, test_user, test_workspace, test_categories, account
):
    food, _, salary = test_categories
    await _row(session, test_user, account, salary, date(2024, 1, 5), "5000", kind="credit")
    await _row(session, test_user, account, salary, date(2024, 2, 5), "5200", kind="credit")
    # A small debit in the income category (a correction) nets against it.
    await _row(session, test_user, account, salary, date(2024, 2, 20), "200")
    await _row(session, test_user, account, food, date(2024, 2, 10), "300")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    [income] = plan.income_rows
    assert income.category_id == salary.id
    assert [c.actual for c in income.cells[:3]] == [Decimal("5000"), Decimal("5000"), Decimal("0")]
    assert income.actual_total == Decimal("10000")
    assert plan.income_actual_by_month[0] == Decimal("5000")
    assert plan.income_actual_total == Decimal("10000")
    assert all(r.category_id != salary.id for r in plan.rows)
    assert plan.actual_by_month[1] == Decimal("300")


@pytest.mark.asyncio
async def test_planned_income_comes_from_recurring_credits(
    session, test_user, test_workspace, test_categories, account
):
    from app.models.recurring_transaction import RecurringTransaction

    _, _, salary = test_categories
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        category_id=salary.id, description="Salary", amount=Decimal("5000"), currency="BRL",
        type="credit", frequency="monthly", day_of_month=5, start_date=date(2024, 3, 5),
        next_occurrence=date(2024, 3, 5), auto_generate=False, is_active=True,
    ))
    # An inactive item and a recurring bill are not income plans.
    session.add(RecurringTransaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        category_id=salary.id, description="Old job", amount=Decimal("9999"), currency="BRL",
        type="credit", frequency="monthly", day_of_month=1, start_date=date(2023, 1, 1),
        next_occurrence=date(2024, 1, 1), auto_generate=False, is_active=False,
    ))
    await session.commit()
    await _row(session, test_user, account, salary, date(2024, 3, 5), "5100", kind="credit")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    [income] = plan.income_rows
    assert income.cells[1].planned is None  # before the item started
    assert [c.planned for c in income.cells[2:]] == [Decimal("5000.00")] * 10
    assert income.cells[2].actual == Decimal("5100")
    assert plan.income_planned_total == Decimal("50000.00")
    # Planned income never shows up in the expense plan.
    assert plan.planned_total == Decimal("0")


@pytest.mark.asyncio
async def test_a_category_in_the_income_group_is_income_even_without_credits_yet(
    session, test_user, test_workspace, account
):
    from app.models.category import Category
    from app.models.category_group import CategoryGroup

    group = CategoryGroup(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, name="Renda")
    session.add(group)
    await session.flush()
    bonus = Category(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                     name="Bônus", group_id=group.id)
    moved = Category(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                     name="Investimentos", group_id=group.id, treat_as_transfer=True)
    session.add_all([bonus, moved])
    await session.commit()
    # A debit in the income group still counts against income, not as spending.
    await _row(session, test_user, account, bonus, date(2024, 6, 1), "50")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert [r.category_id for r in plan.income_rows] == [bonus.id]
    assert plan.income_rows[0].cells[5].actual == Decimal("-50")
    assert all(r.category_id not in (bonus.id, moved.id) for r in plan.rows)


@pytest.mark.asyncio
async def test_a_budgeted_category_with_a_big_refund_stays_in_spending(
    session, test_user, test_workspace, test_categories, account
):
    """More money in than out is not enough to call a budgeted category income."""
    food = test_categories[0]
    session.add(Budget(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                       category_id=food.id, amount=Decimal("500"), month=date(2024, 1, 1)))
    await _row(session, test_user, account, food, date(2024, 1, 10), "300")
    await _row(session, test_user, account, food, date(2024, 2, 10), "900", kind="credit")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert food.id in {r.category_id for r in plan.rows}
    assert food.id not in {r.category_id for r in plan.income_rows}
    assert plan.planned_by_month[0] == Decimal("500")


@pytest.mark.asyncio
async def test_uncategorized_spending_counts_only_your_share_of_a_split(
    session, test_user, test_workspace, account
):
    from app.models.group import Group, GroupMember
    from app.models.transaction_split import TransactionSplit

    group = Group(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                  name="Trip", default_currency="BRL")
    session.add(group)
    await session.flush()
    me = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id,
                     name="Me", linked_user_id=test_user.id, is_self=True)
    friend = GroupMember(id=uuid.uuid4(), group_id=group.id, workspace_id=test_workspace.id,
                         name="Friend", is_self=False)
    session.add_all([me, friend])
    tx = Transaction(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                     account_id=account.id, description="dinner", amount=Decimal("100"),
                     currency="BRL", date=date(2024, 5, 3), type="debit", source="manual",
                     created_at=datetime.now(timezone.utc))
    session.add(tx)
    await session.flush()
    for member, share in ((me, "60"), (friend, "40")):
        session.add(TransactionSplit(id=uuid.uuid4(), transaction_id=tx.id,
                                     workspace_id=test_workspace.id, group_member_id=member.id,
                                     share_amount=Decimal(share), share_type="exact"))
    await session.commit()

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert plan.uncategorized_by_month[4] == Decimal("60")
    assert plan.actual_total == Decimal("60")


@pytest.mark.asyncio
async def test_the_balance_is_real_money_in_minus_out_against_the_plan(
    session, test_user, test_workspace, test_categories, account
):
    food, _, salary = test_categories
    session.add(Budget(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                       category_id=food.id, amount=Decimal("500"), month=date(2024, 1, 1)))
    await _row(session, test_user, account, salary, date(2024, 1, 5), "5000", kind="credit")
    await _row(session, test_user, account, food, date(2024, 1, 10), "300")
    # February: a refund bigger than the month's spending. The grid shows
    # zero spent, but the balance keeps the money that came back.
    await _row(session, test_user, account, food, date(2024, 2, 10), "50")
    await _row(session, test_user, account, food, date(2024, 2, 12), "120", kind="credit")
    await _row(session, test_user, account, None, date(2024, 2, 15), "30")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert plan.net_actual_by_month[0] == Decimal("4700")
    assert plan.net_actual_by_month[1] == Decimal("40")  # +70 refund surplus, -30 uncategorized
    assert plan.net_actual_total == Decimal("4740")
    # Planned: no recurring income, a 500 budget in January.
    assert plan.net_planned_by_month[0] == Decimal("-500")
    assert plan.net_planned_total == Decimal("-500")


async def _transfer_category(session, test_user, test_workspace, name):
    from app.models.category import Category

    category = Category(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                        name=name, treat_as_transfer=True)
    session.add(category)
    await session.commit()
    return category


@pytest.mark.asyncio
async def test_money_moved_to_investments_shows_and_the_account_balance_matches(
    session, test_user, test_workspace, test_categories, account
):
    """Investing is not spending, but the money did leave the accounts: it gets
    its own row, and the account balance takes it out."""
    food, _, salary = test_categories
    invest = await _transfer_category(session, test_user, test_workspace, "Investimentos")
    await _row(session, test_user, account, salary, date(2024, 6, 1), "10000", kind="credit")
    await _row(session, test_user, account, food, date(2024, 6, 5), "1000")
    await _row(session, test_user, account, invest, date(2024, 6, 10), "6000")
    await _row(session, test_user, account, invest, date(2024, 6, 20), "500", kind="credit")
    await _row(session, test_user, account, invest, date(2024, 7, 2), "200", kind="credit")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    [moved] = plan.moved_rows
    assert moved.category_id == invest.id
    assert moved.cells[5].actual == Decimal("5500")    # 6000 put in, 500 taken out
    assert moved.cells[6].actual == Decimal("-200")    # money came back from investments
    assert plan.moved_by_month[5] == Decimal("5500")
    assert plan.moved_total == Decimal("5300")
    # Saved: income minus spending. In the accounts: also minus what moved out.
    assert plan.net_actual_by_month[5] == Decimal("9000")
    assert plan.cash_net_by_month[5] == Decimal("3500")
    assert plan.cash_net_by_month[6] == Decimal("200")
    assert invest.id not in {r.category_id for r in plan.rows + plan.income_rows}


@pytest.mark.asyncio
async def test_a_transfer_between_two_tracked_accounts_does_not_count_as_moved(
    session, test_user, test_workspace, account
):
    from app.models.account import Account

    moves = await _transfer_category(session, test_user, test_workspace, "Transferências")
    savings = Account(id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id,
                      name="Savings", type="savings", balance=Decimal("0"), currency="BRL")
    session.add(savings)
    await session.commit()
    pair = uuid.uuid4()
    for acc, kind in ((account, "debit"), (savings, "credit")):
        session.add(Transaction(
            id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=acc.id,
            category_id=moves.id, description="PIX", amount=Decimal("700"), date=date(2024, 3, 3),
            type=kind, source="manual", transfer_pair_id=pair, created_at=datetime.now(timezone.utc),
        ))
    # Sent to a bank that is not in Securo: the money did leave.
    await _row(session, test_user, account, moves, date(2024, 3, 9), "300")
    await session.commit()

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert plan.moved_by_month[2] == Decimal("300")
    assert plan.cash_net_by_month[2] == Decimal("-300")


@pytest.mark.asyncio
async def test_money_at_month_end_is_what_the_accounts_held(
    session, test_user, test_workspace, test_categories, account
):
    """The real balances on the last day of each month (minus card debt), and
    what they were when the year started, so the plan can show where you
    should be."""
    food, _, salary = test_categories
    await _row(session, test_user, account, salary, date(2023, 12, 20), "500", kind="credit")
    await _row(session, test_user, account, salary, date(2024, 1, 5), "1000", kind="credit")
    await _row(session, test_user, account, food, date(2024, 2, 10), "300")

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert plan.start_money == Decimal("500")
    assert plan.month_end_money[0] == Decimal("1500")
    assert plan.month_end_money[1] == Decimal("1200")
    assert plan.month_end_money[11] == Decimal("1200")


@pytest.mark.asyncio
async def test_a_move_excluded_from_reports_is_not_moved_money(
    session, test_user, test_workspace, account
):
    moves = await _transfer_category(session, test_user, test_workspace, "Transferências")
    session.add(Transaction(
        id=uuid.uuid4(), user_id=test_user.id, workspace_id=test_workspace.id, account_id=account.id,
        category_id=moves.id, description="Dinheiro reservado Buffer", amount=Decimal("4000"),
        date=date(2024, 5, 5), type="debit", source="sync", exclude_from_pnl=True,
        created_at=datetime.now(timezone.utc),
    ))
    await session.commit()

    plan = await get_year_plan(session, test_workspace.id, test_user.id, 2024)

    assert plan.moved_rows == []
    assert plan.moved_total == Decimal("0")
