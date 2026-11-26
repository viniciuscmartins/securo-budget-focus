# Securo, budget-focused fork

This is an **unofficial fork** of [Securo](https://github.com/securo-finance/securo), the open-source, self-hosted personal finance manager. It is not affiliated with or endorsed by the Securo project or its maintainers; for upstream's features, support and releases, use the upstream repository. The fork follows upstream's `main` and adds features for planning a year of spending and for Brazilian accounts. Installation, configuration and everything else work as in upstream: see the [upstream README](../README.md).

**Modified work.** This repository contains changes to Securo made since 2026-10-22. What changed, file by file and pull request by pull request, is in [`CLAUDE.md`](../CLAUDE.md) (map of the fork's files) and [`docs/fork/HISTORY.md`](../docs/fork/HISTORY.md); the git history has every change.

## What the fork adds
- **Annual budget plan** (Budgets page): planned vs actual for every category and month, income, money moved to investments, money at month end, and suggested budgets from your past spending.
- **Rule action "Mark as transfer with account"**: pairs matching transactions with a manual account as transfers.
- **CDI yield** for manual savings accounts: one "Rendimento CDI" entry per day from Banco Central's official rate, gross or net of income tax.
- **Recurring items from transactions**: "Make recurring", a "Link to recurring" rule action, amounts that follow the average of recent payments, and undoing a wrong link.
- **Month outlook** on the dashboard: what is left at month end after expected income, bills and card bills.
- **Grouped sidebar accounts**, with each card's bill to pay and its due date.

The fork adds no database tables or migrations of its own.

## Images
`ghcr.io/viniciuscmartins/securo-backend` and `ghcr.io/viniciuscmartins/securo-frontend`, tagged `<upstream version>-plan.<N>` and `latest`. Each release lists its upgrade notes.

Every service built on the backend image must use the fork's: `backend`, `migrate`, `celery-worker`, `celery-beat`, `mcp-server`, and, with the `plugins` profile on, `plugins-runner` and `plugins-egress`. Mixing images runs two different versions against one database.

To switch an existing upstream install without editing upstream's compose file, put a `compose.override.yaml` next to it (`docker-compose.override.yml` if the main file is `docker-compose.yml`). Docker Compose reads it automatically; in Dockge, place it in the stack's folder. Pin a release tag rather than `latest` (image tags have no leading `v`: release `v0.22.0-plan.1` is image tag `0.22.0-plan.1`):

```yaml
x-fork-backend: &fork-backend
  image: ghcr.io/viniciuscmartins/securo-backend:0.22.0-plan.1
services:
  backend: *fork-backend
  migrate: *fork-backend
  celery-worker: *fork-backend
  celery-beat: *fork-backend
  mcp-server: *fork-backend
  plugins-runner: *fork-backend   # only used with the plugins profile
  plugins-egress: *fork-backend   # only used with the plugins profile
  frontend:
    image: ghcr.io/viniciuscmartins/securo-frontend:0.22.0-plan.1
```

Data stays in the stack's named volumes (`pgdata`, `attachments`, `plugins`), so keep the same stack (project) name and the same `.env` when switching. Back up the database first, as for any upgrade.

## Known limitations
- **Backups:** the fork keeps CDI yield settings and "follow the average" amount modes as instance settings (`app_settings` keys `cdi_yield:<account id>` and `recurring_amount:<recurring id>`). Upstream's workspace backup and restore does not carry them: after restoring into a new instance, turn CDI yield and averaged amounts back on by hand. A database dump (`pg_dump`) keeps them.
- **Going back to upstream images:** fork-only rule actions (`transfer_to_account`, `link_recurring`) stay in the rules. Upstream skips them when rules run, but refuses to save a rule that still has one: remove those actions before editing such a rule.

## For contributors
- [`CLAUDE.md`](../CLAUDE.md): map of the fork's files and rules.
- [`docs/fork/CONTRIBUTING.md`](../docs/fork/CONTRIBUTING.md): process, checks and security checklist.
- [`docs/fork/HISTORY.md`](../docs/fork/HISTORY.md): what each change did and why.
- Security issues in the fork's own code: see [`SECURITY.md`](SECURITY.md).

License: AGPL-3.0, as upstream. Securo is a project of its upstream authors; the name is used here only to say what this fork is based on.
