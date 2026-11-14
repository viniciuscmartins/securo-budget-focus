# Plan: move fork changes out of upstream files

Approved 2026-11-14, not yet applied. Goal: fewer merge conflicts with upstream.
Baseline at approval: the fork changed 47 upstream files in 201 hunks (+3,864 lines).
Target: about 28 files and 45 to 50 hunks, most of them one-line hooks.
Fork-only files stay where they are (upstream never touches them). Start after the open sync PR is merged.
One PR per phase, CI green on each, behaviour unchanged.

## Phase 1: no behaviour change (201 → ~85 hunks)
| Move | From → to | Hunks |
|---|---|---|
| Fork strings | 18 `frontend/src/locales/*.json` → `frontend/src/fork/locales/*.json` (all 18, existing translations kept), deep-merged in `lib/i18n.ts`; a fork copy of the locale parity tests covers them | 90 → 2 |
| Contract baseline | 39 fork entries → `backend/tests/api_contract/baseline.fork.json`, loaded in `conftest.py` | 22 → 1 |
| API client | `lib/api.ts` fork clients → `src/fork/api.ts` (upstream's `api` instance exported) | 4 → 1 |
| Types | `types/index.ts` → `src/fork/types.ts`, `RecurringTransaction` fields by module augmentation | 3 → 0–1 |
| Annual plan API | `schemas/budget.py` block → `schemas/budget_plan.py`; routes → `api/budget_plan.py` (prefix `/api/budgets/year-plan`, same URLs) | 5 → 0 |
| Routers | `main.py` → `app/fork/__init__.py: register(app)` | 2 → 1 |
| Beat jobs | `worker.py` → `app/fork/celery.py: extend(celery_app)` at end of file | 2 → 1 |

## Phase 2: backend rules (22 → ~7 hunks; money logic, high effort)
| Move | From → to | Hunks |
|---|---|---|
| Rule actions | `rule_service.py` ops, validation, apply, preview → `services/fork_rule_actions.py`, ~5 one-line hooks | 14 → 5 |
| Rule engine | `rule_engine.py` constants and targets → same module, one dispatch line | 2 → 1 |
| Transfer by rule (option A) | `transaction_service.py`: upstream `create_transfer_counterpart` restored as upstream has it; `transfer_by_rule`, `_has_paired_twin` and a copy of the counterpart builder → `services/rule_transfer_service.py`, with a parity test against the "create counterpart" button | 6 → 1 |
| Schema comment | `schemas/rule.py` | 1 → 0 |

## Phase 3: frontend rules and recurring (27 → ~10 hunks)
| Move | From → to | Hunks |
|---|---|---|
| Rule UI | `rule-dialog.tsx`, `rules.tsx` → `src/fork/rule-actions.tsx` | 14 → ~5 |
| Recurring UI | `recurring.tsx`, `transaction-dialog.tsx` → `src/fork/recurring-extras.tsx` | 13 → ~5 |

## Stays as is (small and deliberate)
`App.tsx` routes, command palette, `budgets.tsx` link, dashboard and account-detail slots, `library.ts`,
`dashboard-widgets.tsx`, `app-layout.tsx`, `access_token_scopes.py` (security: kept visible),
`dashboard_service.py`, `recurring_transaction_service.py` hooks, `schemas/recurring_transaction.py`, `.gitignore`.

After each phase: update `CLAUDE.md` (file map) and the sync task prompt.
