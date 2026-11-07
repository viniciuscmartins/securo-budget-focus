import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.app_clock import use_timezone
from app.core.config import get_settings
from app.worker import celery_app

logger = logging.getLogger(__name__)


async def _refresh() -> int:
    from app.services.recurring_link_service import refresh_all

    engine = create_async_engine(get_settings().database_url)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_maker() as session, use_timezone(session, fresh=True):
            return await refresh_all(session)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.recurring_link_tasks.refresh_recurring_amounts")
def refresh_recurring_amounts() -> dict:
    """Celery task: re-average recurring items that follow their payments.

    Picks up payments linked during a bank sync or by hand. Idempotent.
    """
    try:
        changed = asyncio.run(_refresh())
    except Exception as exc:  # noqa: BLE001 - DB trouble; try next run
        logger.warning("Recurring amount refresh failed: %s", exc)
        return {"changed": 0, "error": str(exc)}
    return {"changed": changed}
