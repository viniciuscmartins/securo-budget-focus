"""Annual budget plan routes (fork feature): /api/budgets/year-plan.

Kept out of upstream's api/budgets.py so upstream merges don't touch it.
Upstream's /api/budgets/{budget_id} routes are PATCH and DELETE only, so they
never catch these paths.
"""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.budget_plan import (
    BudgetYearApply,
    BudgetYearApplyResult,
    BudgetYearPlan,
    BudgetYearSuggestion,
)
from app.services import budget_plan_service

router = APIRouter(prefix="/api/budgets/year-plan", tags=["budgets"])


@router.get("", response_model=BudgetYearPlan)
async def year_plan(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Planned vs executed spending for every category and month of a year."""
    return await budget_plan_service.get_year_plan(session, ctx.workspace.id, ctx.user_id, year)


@router.get("/suggestion", response_model=BudgetYearSuggestion)
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


@router.put("", response_model=BudgetYearApplyResult)
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
