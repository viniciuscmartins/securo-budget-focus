# Securo fork: facts for Claude sessions

Fork = upstream `securo-finance/securo` `main` + the fork features below. Rules, process and the OWASP
checklist: `docs/fork/CONTRIBUTING.md` (read it before changing code). PR write-ups: `docs/fork/HISTORY.md`.
Start every reply to a task with `**Effort: <low|medium|high> recommended** (why)`, per CONTRIBUTING.md.

## Stack and commands
- Backend `backend/`: Python ≥3.11, FastAPI, SQLAlchemy 2 (async), Alembic, Celery + Redis, uv. Prod DB PostgreSQL.
- Frontend `frontend/`: React 19, TypeScript, Vite, TanStack Query, Tailwind 4, vitest. Run Docker via `docker-compose.yml`.
- Backend checks: `cd backend && uv sync --frozen --extra dev && uv run ruff check app tests && uv run ty check . && uv run pytest -q -n 4 -W error`
- Contract baseline: `UPDATE_API_CONTRACT=1 uv run pytest tests/api_contract -n 0 -q` writes upstream's entries to `baseline.json`
  and the fork's 39 to `baseline.fork.json` (split by `tests/api_contract/fork_baseline.py`).
- Frontend checks: `cd frontend && npm ci && npm run typecheck && npx eslint <files> && npx vitest run <files>`
- CI (`.github/workflows/ci.yml`, identical to upstream's) runs backend on PostgreSQL + frontend checks on every PR and push to main.

## Upstream layout
- `backend/app/{api,schemas,services,models,tasks,providers,core}`; routers registered in `app/main.py`,
  beat jobs in `app/worker.py`, token scopes in `app/core/access_token_scopes.py` (deny by default).
- `frontend/src/{pages,components,lib,hooks,types,locales}`; API client `lib/api.ts`, types `types/index.ts`, routes `App.tsx`.
- Fork wiring: `backend/app/fork/` (`include_fork_routers` called in `main.py`, `extend_celery` in `worker.py`);
  `frontend/src/fork/` (`api.ts` clients on upstream's `api`, `types.ts`, `i18n.ts`, `locales/`). Put new fork code there, not in upstream files.

## Fork features (all fork-only files unless marked "shared")
1. Annual budget plan. `services/budget_plan_service.py`, `pages/budget-plan.tsx`, `components/budget-suggestion-dialog.tsx`,
   `lib/budget-plan-utils.ts`, `api/budget_plan.py` (`/api/budgets/year-plan` GET, `/suggestion`, PUT),
   `schemas/budget_plan.py`, client `budgetPlan` in `src/fork/api.ts`. Routes: `/budgets` = plan, `/budgets/monthly` = upstream page, `/budgets/plan` → `/budgets`.
   Uses upstream `_query_filters`, `report_service._net_worth_at`; numbers must match the Budgets page.
2. Rule action `transfer_to_account` ("Mark as transfer with account"). `services/fork_rule_actions.py` (both fork actions:
   validation, targets, preview, apply), `services/rule_transfer_service.py` (`transfer_by_rule`, its copy of upstream's
   counterpart builder; `tests/test_rule_transfer_parity.py` fails if upstream's `create_transfer_counterpart` changes).
   Shared: one-line hooks in `rule_service.py` (`fork_rules.*`), `components/rule-dialog.tsx`, `pages/rules.tsx`.
3. CDI yield. `providers/cdi.py` (only host `api.bcb.gov.br`), `services/cdi_yield_service.py`, `api/cdi_yield.py`,
   `tasks/cdi_yield_tasks.py`, `components/cdi-yield-card.tsx` (in `account-detail.tsx`).
4. Recurring links (`link_recurring` rule action, Make recurring, average amount, unlink step-back).
   `services/recurring_link_service.py`, `api/recurring_links.py`, `tasks/recurring_link_tasks.py`, `components/recurring-link.tsx`.
   Shared: `rule_service.py` hooks, `recurring_transaction_service.py`, `transaction_service.unlink_recurring_transaction` (calls `unlink_and_step_back`),
   `pages/recurring.tsx`, `transaction-dialog.tsx`.
5. Month outlook. `services/month_outlook_service.py`, `api/month_outlook.py` (`/api/month-outlook`, `/card-bills`),
   `components/month-outlook-card.tsx` (in `dashboard.tsx`). Shared: `dashboard_service._get_recurring_projections`.
6. Sidebar account groups. `components/sidebar-account-groups.tsx`, `lib/sidebar-account-totals.ts`; `app-layout.tsx` renders it.
- Tests: `backend/tests/test_{budget_plan_*,cdi_yield,recurring_links,month_outlook,rule_transfer_action,fork_access_token_scopes}.py`, `*.test.tsx` next to each file.
- DB: **no fork migrations or tables.** Settings in `app_settings` keys `cdi_yield:<account id>`, `recurring_amount:<recurring id>`.
  Migrations in `backend/alembic/versions` are all upstream.
- i18n: fork strings only in `frontend/src/fork/locales/<lang>.json` (all 18 languages), merged by `src/fork/i18n.ts`;
  never add them to `src/locales`. Blocks `budgetPlan`, `cdiYield`, `recurringLink`, `monthOutlook`, `rules.transferToAccount*`, `recurring.nextDate*`, `accounts.sidebar*`.

## Don't touch
- Upstream auth, password rules, rate limits; upstream migrations; version numbers (`pyproject.toml`, `package.json`).
- Existing translations (never replace with English). `src/locales/*.json` and `baseline.json` stay identical to upstream's.
- `CLAUDE.md` and `docs/fork/` are fork-only: keep them in every sync (upstream `.gitignore` lists `CLAUDE.md`).
- Don't run `ruff format` on the whole tree, only on fork files.
- Sync report: `scripts/fork/sync-report.sh` (upstream changes in fork-touched files, migrations, deploy files). Refactor plan: `docs/fork/REFACTOR-PLAN.md`.

## Standing rules (short)
- Every commit's author and committer is `viniciuscmartins` <139562176+viniciuscmartins@users.noreply.github.com>,
  Claude's included (credit Claude with a `Co-Authored-By` trailer). No personal data or session links anywhere.
- The repo is **public**. Before every push, PR, comment or release text: run `scripts/fork/privacy-check.sh`
  (private terms such as bank or account names go in `.git/info/privacy-terms`, never in the repo)
  (CI runs it too, as "Fork privacy check") and reread the text for personal data: emails, session/chat links,
  banks, account names, balances, location, employer, home or network setup. Use generic names in tests and docs.
- Releases: tag `v<upstream version>-plan.<N>`. Don't merge PRs or publish releases unless the owner asks.
- New strings in English + pt-BR + pt-PT; other locales get English.
