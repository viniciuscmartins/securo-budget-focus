"""CDI yield settings for a manual savings account."""

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_session
from app.core.workspace_context import (
    WorkspaceContext,
    current_workspace,
    current_writable_workspace,
)
from app.schemas.cdi_yield import CdiYieldRead, CdiYieldRunResult, CdiYieldSettingsWrite
from app.services import cdi_yield_service as svc

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


async def _read(session: AsyncSession, account_id: uuid.UUID) -> CdiYieldRead:
    settings = await svc.get_settings(session, account_id)
    last, total = await svc.summary(session, account_id)
    if settings is None:
        return CdiYieldRead(configured=False, last_posted_date=last, total_posted=total)
    return CdiYieldRead(
        configured=True,
        enabled=settings.enabled,
        percent=settings.percent,
        mode=settings.mode,
        start_date=settings.start_date,
        last_posted_date=last,
        total_posted=total,
    )


def _rates_unavailable(exc: Exception) -> HTTPException:
    logger.warning("CDI rates unavailable: %s", exc)
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail="Could not reach Banco Central for CDI rates; nothing was changed. Try again later.",
    )


@router.get("/{account_id}/cdi-yield", response_model=CdiYieldRead)
async def get_cdi_yield(
    account_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    try:
        await svc.eligible_account(session, ctx.workspace.id, account_id)
    except svc.CdiYieldError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND if "not found" in str(e) else 400, detail=str(e))
    return await _read(session, account_id)


@router.put("/{account_id}/cdi-yield", response_model=CdiYieldRunResult)
async def put_cdi_yield(
    account_id: uuid.UUID,
    data: CdiYieldSettingsWrite,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Save the settings and rebuild the yield from the start date."""
    settings = svc.CdiSettings(
        enabled=data.enabled, percent=data.percent, mode=data.mode, start_date=data.start_date
    )
    try:
        posted = await svc.save_settings(session, ctx.workspace.id, account_id, settings)
    except svc.CdiYieldError as e:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND if "not found" in str(e) else 400, detail=str(e))
    except httpx.HTTPError as e:
        await session.rollback()
        raise _rates_unavailable(e)
    return CdiYieldRunResult(posted_days=posted, settings=await _read(session, account_id))


@router.post("/{account_id}/cdi-yield/recalculate", response_model=CdiYieldRunResult)
async def recalculate_cdi_yield(
    account_id: uuid.UUID,
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Recompute all posted yield, e.g. after past deposits were added."""
    try:
        posted = await svc.recalculate(session, ctx.workspace.id, account_id)
    except svc.CdiYieldError as e:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND if "not found" in str(e) else 400, detail=str(e))
    except httpx.HTTPError as e:
        await session.rollback()
        raise _rates_unavailable(e)
    return CdiYieldRunResult(posted_days=posted, settings=await _read(session, account_id))


@router.delete("/{account_id}/cdi-yield", status_code=status.HTTP_204_NO_CONTENT)
async def delete_cdi_yield(
    account_id: uuid.UUID,
    delete_posted: bool = Query(False),
    ctx: WorkspaceContext = Depends(current_writable_workspace),
    session: AsyncSession = Depends(get_async_session),
):
    """Turn CDI yield off; posted yield stays unless `delete_posted=true`."""
    try:
        await svc.remove_settings(session, ctx.workspace.id, account_id, delete_posted)
    except svc.CdiYieldError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND if "not found" in str(e) else 400, detail=str(e))
