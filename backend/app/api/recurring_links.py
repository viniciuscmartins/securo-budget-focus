"""Make recurring items from transactions, and let their amount follow payments."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.recurring_link import (
    AmountModeRead,
    AmountModeWrite,
    RecurringFromTransaction,
    RecurringFromTransactionResult,
)
from app.schemas.recurring_transaction import RecurringTransactionRead
from app.services import recurring_link_service as svc

router = APIRouter(prefix="/api/recurring-links", tags=["recurring-transactions"])


def _http(exc: svc.RecurringLinkError) -> HTTPException:
    code = (
        status.HTTP_404_NOT_FOUND
        if isinstance(exc, svc.RecurringLinkNotFound)
        else status.HTTP_400_BAD_REQUEST
    )
    return HTTPException(status_code=code, detail=str(exc))


@router.post(
    "/from-transaction/{transaction_id}",
    response_model=RecurringFromTransactionResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_transaction(
    transaction_id: uuid.UUID,
    data: RecurringFromTransaction,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Turn a transaction into the first payment of a new recurring item."""
    rule = (
        svc.LinkRuleSpec(
            field=data.link_rule.field, op=data.link_rule.op, value=data.link_rule.value
        )
        if data.link_rule is not None
        else None
    )
    try:
        recurring, linked = await svc.create_from_transaction(
            session,
            ctx.workspace.id,
            ctx.user_id,
            transaction_id,
            frequency=data.frequency,
            description=data.description,
            day_of_month=data.day_of_month,
            category_id=data.category_id,
            follow_average=data.follow_average,
            link_rule=rule,
        )
    except svc.RecurringLinkError as e:
        await session.rollback()
        raise _http(e)
    except ValueError as e:  # account not in this workspace
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return RecurringFromTransactionResult(
        recurring=RecurringTransactionRead.model_validate(recurring), linked_history=linked
    )


@router.get("/amount-modes", response_model=list[AmountModeRead])
async def list_amount_modes(
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Recurring items whose amount follows the average of their payments."""
    modes = await svc.amount_modes(session, ctx.workspace.id)
    return [
        AmountModeRead(recurring_id=rid, mode=m.mode, window=m.window) for rid, m in modes.items()
    ]


@router.put("/{recurring_id}/amount-mode", response_model=RecurringTransactionRead)
async def put_amount_mode(
    recurring_id: uuid.UUID,
    data: AmountModeWrite,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Follow the average of the last payments (applied now), or keep the amount fixed."""
    mode = svc.AmountMode(window=data.window) if data.follow_average else None
    try:
        return await svc.set_amount_mode(session, ctx.workspace.id, recurring_id, mode)
    except svc.RecurringLinkError as e:
        await session.rollback()
        raise _http(e)
