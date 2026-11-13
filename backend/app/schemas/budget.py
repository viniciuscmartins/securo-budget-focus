import uuid
from datetime import date as _Date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class BudgetCreate(BaseModel):
    category_id: uuid.UUID
    amount: Decimal
    month: _Date  # First day of month
    is_recurring: bool = False


class BudgetUpdate(BaseModel):
    amount: Optional[Decimal] = None
    effective_month: Optional[_Date] = None


class BudgetRead(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    category_id: uuid.UUID
    amount: Decimal
    month: _Date
    is_recurring: bool

    model_config = ConfigDict(from_attributes=True)


class BudgetVsActual(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    budget_amount: Optional[Decimal] = None
    actual_amount: Decimal
    projected_amount: Decimal = Decimal("0")
    prev_month_amount: Decimal = Decimal("0")
    projected_prev_month_amount: Decimal = Decimal("0")
    percentage_used: Optional[float] = None
    is_recurring: bool = False


class BudgetSuggestion(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    suggested_amount: Decimal
    total_spent: Decimal
    months_with_data: int
    existing_budget: bool = False


class BudgetSuggestionResponse(BaseModel):
    month: _Date
    months_requested: int
    months_analyzed: int
    suggestions: list[BudgetSuggestion]


class BudgetBatchItem(BaseModel):
    category_id: uuid.UUID
    amount: Decimal = Field(gt=0)


class BudgetBatchCreate(BaseModel):
    month: _Date
    is_recurring: bool = False
    items: list[BudgetBatchItem] = Field(min_length=1, max_length=200)


class BudgetCopyMonthRequest(BaseModel):
    source_month: _Date
    target_month: _Date
    adjustment_percentage: Decimal = Field(
        default=Decimal("0.0"), ge=Decimal("-100"), le=Decimal("1000")
    )
    overwrite_existing: bool = False


class BudgetRolloverCategory(BaseModel):
    category_id: uuid.UUID
    category_name: str
    budgeted: Decimal
    actual_spent: Decimal
    projected_spent: Decimal
    remaining: Decimal
    carryover_eligible: bool


class BudgetRolloverSummaryResponse(BaseModel):
    month: _Date
    total_budgeted: Decimal
    total_spent: Decimal
    total_projected_spent: Decimal
    net_surplus: Decimal
    categories: list[BudgetRolloverCategory]


class BudgetMultiMonthForecastItem(BaseModel):
    month: _Date
    projected_budget: Decimal
    projected_spend: Decimal
    projected_variance: Decimal


class BudgetForecastResponse(BaseModel):
    forecast_months: int
    items: list[BudgetMultiMonthForecastItem]


# ---------------------------------------------------------------------------
# Annual plan: planned vs executed per category and month
# ---------------------------------------------------------------------------


class BudgetYearCell(BaseModel):
    month: int  # 1..12
    planned: Optional[Decimal] = None  # None = no budget set for this month
    actual: Decimal = Decimal("0")


class BudgetYearRow(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    cells: list[BudgetYearCell]  # always 12 entries, January first
    planned_total: Decimal
    actual_total: Decimal


class BudgetYearPlan(BaseModel):
    year: int
    rows: list[BudgetYearRow]  # expense categories
    planned_by_month: list[Decimal]  # 12 entries, expenses
    actual_by_month: list[Decimal]  # 12 entries, expenses incl. uncategorized
    planned_total: Decimal
    actual_total: Decimal
    # Spending with no category: actuals only (it can't have a budget).
    uncategorized_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    uncategorized_total: Decimal = Decimal("0")
    # Income categories: planned from recurring credit items, actual from
    # credits minus debits. Amounts are positive for money coming in.
    income_rows: list[BudgetYearRow] = Field(default_factory=list)
    # Balance: money in minus money out. Actual is the real one (refunds
    # included); planned is planned income minus budgets.
    net_actual_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    net_planned_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    net_actual_total: Decimal = Decimal("0")
    net_planned_total: Decimal = Decimal("0")
    # What the accounts held (balances minus card and loan debt) when the year
    # started and at the end of each month; None for months ahead.
    start_money: Decimal = Decimal("0")
    month_end_money: list[Optional[Decimal]] = Field(default_factory=lambda: [None] * 12)
    # Money moved through transfer-like categories (investments, transfers to
    # banks Securo does not track), not counted as spending: debits minus
    # credits. The account balance is the balance minus what moved.
    moved_rows: list[BudgetYearRow] = Field(default_factory=list)
    moved_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    moved_total: Decimal = Decimal("0")
    cash_net_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    cash_net_total: Decimal = Decimal("0")
    # Money received with no category (included in income_actual_by_month).
    uncategorized_income_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    uncategorized_income_total: Decimal = Decimal("0")
    income_planned_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    income_actual_by_month: list[Decimal] = Field(default_factory=lambda: [Decimal("0")] * 12)
    income_planned_total: Decimal = Decimal("0")
    income_actual_total: Decimal = Decimal("0")


class BudgetSuggestionRow(BaseModel):
    category_id: uuid.UUID
    category_name: str
    category_icon: str
    category_color: str
    group_id: Optional[uuid.UUID] = None
    group_name: Optional[str] = None
    history_average: Decimal  # mean monthly spend over the lookback window
    history_median: Decimal
    proposed: list[Decimal]  # 12 entries, January first
    existing: list[Optional[Decimal]]  # budget currently in effect, 12 entries


class BudgetYearSuggestion(BaseModel):
    year: int
    method: Literal["median", "average", "seasonal"]
    lookback_months: int
    history_start: _Date  # first day of the oldest month used
    history_end: _Date  # first day of the newest month used
    rows: list[BudgetSuggestionRow]


# Largest value the budgets.amount column (NUMERIC(15, 2)) can hold. Checked
# here so an oversized amount is a 422, not a database overflow and a 500.
MAX_BUDGET_AMOUNT = Decimal("9999999999999.99")


class BudgetPlanCellWrite(BaseModel):
    category_id: uuid.UUID
    month: int = Field(ge=1, le=12)
    # None removes the month-specific budget (a recurring default, if any,
    # applies again). Zero is a valid budget.
    amount: Optional[Decimal] = Field(default=None, ge=0, le=MAX_BUDGET_AMOUNT)


class BudgetYearApply(BaseModel):
    year: int = Field(ge=2000, le=2100)
    cells: list[BudgetPlanCellWrite] = Field(max_length=12 * 500)


class BudgetYearApplyResult(BaseModel):
    saved: int
    deleted: int
