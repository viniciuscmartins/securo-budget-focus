"""Month outlook: money now, what comes in and goes out, and what is left."""

import uuid
from dataclasses import asdict
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import WorkspaceContext, current_workspace
from app.schemas.month_outlook import CardBillRead, CashAccountRead, MonthOutlookRead
from app.services import month_outlook_service as svc

router = APIRouter(prefix="/api/month-outlook", tags=["dashboard"])


@router.get("", response_model=MonthOutlookRead)
async def get_month_outlook(
    month: Optional[date] = Query(None, description="Any day of the month; defaults to this month"),
    account_ids: Optional[list[uuid.UUID]] = Query(
        None, description="Only these accounts (a Collection); all open accounts when omitted"
    ),
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        outlook = await svc.get_outlook(
            session, ctx.workspace.id, ctx.user_id, month, account_ids
        )
    except svc.MonthOutlookError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    data = asdict(outlook)
    data["cash_accounts"] = [
        CashAccountRead(account_id=aid, account_name=name, balance=balance)
        for aid, name, balance in outlook.cash_accounts
    ]
    return data


@router.get("/card-bills", response_model=list[CardBillRead])
async def get_next_card_bills(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Each credit card's next bill with something still to pay (for the sidebar)."""
    bills = await svc.get_next_card_bills(session, ctx.workspace.id, ctx.user_id)
    return [asdict(b) for b in bills]
