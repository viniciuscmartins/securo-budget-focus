import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.app_clock import use_timezone
from app.core.config import get_settings
from app.worker import celery_app

logger = logging.getLogger(__name__)


async def _post_cdi_yield() -> int:
    from app.services.cdi_yield_service import run_all

    engine = create_async_engine(get_settings().database_url)
    session_maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_maker() as session, use_timezone(session, fresh=True):
            return await run_all(session)
    finally:
        await engine.dispose()


@celery_app.task(name="app.tasks.cdi_yield_tasks.post_cdi_yield")
def post_cdi_yield() -> dict:
    """Celery task: post the CDI yield that is due on every account set up for it.

    Idempotent: a day already posted is never posted again, so running it
    several times a day only catches up as soon as Banco Central publishes.
    """
    try:
        posted = asyncio.run(_post_cdi_yield())
    except Exception as exc:  # noqa: BLE001 - network or DB trouble; try next run
        logger.warning("CDI yield run failed: %s", exc)
        return {"posted": 0, "error": str(exc)}
    return {"posted": posted}
