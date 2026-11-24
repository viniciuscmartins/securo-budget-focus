# Securo, budget-focused fork

This is a fork of [Securo](https://github.com/securo-finance/securo), the open-source, self-hosted personal finance manager. It follows upstream's `main` and adds features for planning a year of spending and for Brazilian accounts. Installation, configuration and everything else work as in upstream: see the [upstream README](../README.md).

## What the fork adds
- **Annual budget plan** (Budgets page): planned vs actual for every category and month, income, money moved to investments, money at month end, and suggested budgets from your past spending.
- **Rule action "Mark as transfer with account"**: pairs matching transactions with a manual account as transfers.
- **CDI yield** for manual savings accounts: one "Rendimento CDI" entry per day from Banco Central's official rate, gross or net of income tax.
- **Recurring items from transactions**: "Make recurring", a "Link to recurring" rule action, amounts that follow the average of recent payments, and undoing a wrong link.
- **Month outlook** on the dashboard: what is left at month end after expected income, bills and card bills.
- **Grouped sidebar accounts**, with each card's bill to pay and its due date.

The fork adds no database tables or migrations of its own.

## Images
`ghcr.io/viniciuscmartins/securo-backend` and `ghcr.io/viniciuscmartins/securo-frontend`, tagged `<upstream version>-plan.<N>` and `latest`. Every backend-based service (backend, migrate, celery-worker, celery-beat, mcp-server) must use the fork's backend image. Each release lists its upgrade notes.

## For contributors
- [`CLAUDE.md`](../CLAUDE.md): map of the fork's files and rules.
- [`docs/fork/CONTRIBUTING.md`](../docs/fork/CONTRIBUTING.md): process, checks and security checklist.
- [`docs/fork/HISTORY.md`](../docs/fork/HISTORY.md): what each change did and why.

License: AGPL-3.0, as upstream.
