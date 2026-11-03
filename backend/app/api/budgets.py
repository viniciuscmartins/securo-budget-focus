import uuid
from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.budget import (
    BudgetBatchCreate,
    BudgetCopyMonthRequest,
    BudgetCreate,
    BudgetForecastResponse,
    BudgetRead,
    BudgetRolloverSummaryResponse,
    BudgetSuggestionResponse,
    BudgetUpdate,
    BudgetVsActual,
    BudgetYearApply,
    BudgetYearApplyResult,
    BudgetYearPlan,
    BudgetYearSuggestion,
)
from app.services import budget_plan_service, budget_service

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetRead])
async def list_budgets(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await budget_service.get_budgets(session, ctx.workspace.id, month)


@router.post("", response_model=BudgetRead, status_code=status.HTTP_201_CREATED)
async def create_budget(
    data: BudgetCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await budget_service.create_budget(session, ctx.workspace.id, ctx.user_id, data)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post(
    "/batch", response_model=list[BudgetRead], status_code=status.HTTP_201_CREATED
)
async def create_budgets_batch(
    data: BudgetBatchCreate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        return await budget_service.create_budgets_batch(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/suggestions", response_model=BudgetSuggestionResponse)
async def budget_suggestions(
    month: Optional[date] = Query(None),
    months: int = Query(3, ge=1, le=24),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await budget_service.get_budget_suggestions(
        session, ctx.workspace.id, ctx.user_id, month, months
    )


@router.patch("/{budget_id}", response_model=BudgetRead)
async def update_budget(
    budget_id: uuid.UUID,
    data: BudgetUpdate,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    budget = await budget_service.update_budget(session, budget_id, ctx.workspace.id, data)
    if not budget:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")
    return budget


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(
    budget_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    deleted = await budget_service.delete_budget(session, budget_id, ctx.workspace.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Budget not found")


@router.get("/comparison", response_model=list[BudgetVsActual])
async def budget_comparison(
    month: Optional[date] = Query(None),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    return await budget_service.get_budget_vs_actual(session, ctx.workspace.id, ctx.user_id, month)


@router.post("/copy-month", response_model=list[BudgetRead])
async def copy_budgets_endpoint(
    req: BudgetCopyMonthRequest,
    db: AsyncSession = Depends(get_async_session),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
):
    """Copy all budget category allocations from a source month to a target month with optional percentage adjustments."""
    try:
        return await budget_service.copy_monthly_budgets(
            db=db,
            workspace_id=ctx.workspace.id,
            user_id=ctx.user_id,
            source_month=req.source_month,
            target_month=req.target_month,
            adjustment_percentage=req.adjustment_percentage,
            overwrite_existing=req.overwrite_existing,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/rollover-summary", response_model=BudgetRolloverSummaryResponse)
async def get_rollover_summary_endpoint(
    month: date = Query(...),
    db: AsyncSession = Depends(get_async_session),
    ctx: WorkspaceContext = Depends(current_workspace),
):
    """Calculate end-of-month surpluses and deficits for budget carryover analysis."""
    return await budget_service.get_budget_rollover_summary(
        db=db,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        month=month,
    )


@router.get("/forecast", response_model=BudgetForecastResponse)
async def get_budget_forecast_endpoint(
    start_month: date = Query(...),
    months: int = Query(6, ge=1, le=24),
    db: AsyncSession = Depends(get_async_session),
    ctx: WorkspaceContext = Depends(current_workspace),
):
    """Project multi-month budget performance and variance."""
    return await budget_service.get_multi_month_forecast(
        db=db,
        workspace_id=ctx.workspace.id,
        user_id=ctx.user_id,
        start_month=start_month,
        num_months=months,
    )


@router.get("/year-plan", response_model=BudgetYearPlan)
async def year_plan(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Planned vs executed spending for every category and month of a year."""
    return await budget_plan_service.get_year_plan(session, ctx.workspace.id, ctx.user_id, year)


@router.get("/year-plan/suggestion", response_model=BudgetYearSuggestion)
async def year_plan_suggestion(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    lookback_months: int = Query(12, ge=3, le=24),
    method: Literal["median", "average", "seasonal"] = Query("median"),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Proposed monthly budget per category, derived from recent spending.

    Read-only: nothing is saved until the client sends the reviewed numbers
    to ``PUT /year-plan``.
    """
    return await budget_plan_service.suggest_year_budget(
        session, ctx.workspace.id, ctx.user_id, year, lookback_months, method
    )


@router.put("/year-plan", response_model=BudgetYearApplyResult)
async def apply_year_plan(
    data: BudgetYearApply,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Bulk-save month-specific budgets (``amount: null`` clears a month)."""
    try:
        return await budget_plan_service.apply_year_plan(
            session, ctx.workspace.id, ctx.user_id, data
        )
    except budget_plan_service.PlanError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except budget_plan_service.PlanConflict as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))
