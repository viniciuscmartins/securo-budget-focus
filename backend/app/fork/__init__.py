"""Fork-only wiring, kept out of upstream files so upstream merges stay clean.

Upstream's app/main.py and app/worker.py each call one function from here.
See docs/fork/REFACTOR-PLAN.md.
"""

from fastapi import FastAPI

from app.api.budget_plan import router as budget_plan_router
from app.api.cdi_yield import router as cdi_yield_router
from app.api.month_outlook import router as month_outlook_router
from app.api.recurring_links import router as recurring_links_router

FORK_ROUTERS = (
    budget_plan_router,
    cdi_yield_router,
    recurring_links_router,
    month_outlook_router,
)


def include_fork_routers(app: FastAPI) -> None:
    """Register the fork's API routers (access-token scopes for their prefixes
    live in app/core/access_token_scopes.py)."""
    for router in FORK_ROUTERS:
        app.include_router(router)
