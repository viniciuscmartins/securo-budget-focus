"""The fork's Celery tasks and beat entries, added to upstream's worker."""

from celery import Celery

FORK_TASK_MODULES = (
    "app.tasks.cdi_yield_tasks",
    "app.tasks.recurring_link_tasks",
)

FORK_BEAT_SCHEDULE = {
    "post-cdi-yield": {
        "task": "app.tasks.cdi_yield_tasks.post_cdi_yield",
        # Every 6 hours: Banco Central publishes a day's CDI the next
        # business day, at no fixed hour. Idempotent, so extra runs only
        # pick it up sooner; one rate request per run covers all accounts.
        "schedule": 60 * 60 * 6,
    },
    "refresh-recurring-amounts": {
        "task": "app.tasks.recurring_link_tasks.refresh_recurring_amounts",
        # Hourly: re-averages recurring items whose amount follows their
        # payments, after a bank sync linked a new one. Cheap and idempotent.
        "schedule": 60 * 60,
    },
}


def extend_celery(celery_app: Celery) -> None:
    celery_app.conf.beat_schedule.update(FORK_BEAT_SCHEDULE)
    celery_app.conf.include = [
        *celery_app.conf.include,
        *(m for m in FORK_TASK_MODULES if m not in celery_app.conf.include),
    ]
