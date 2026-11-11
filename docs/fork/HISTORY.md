# Fork history

The write-ups of the pull requests that built this fork, kept here because pull requests do not survive leaving the fork network. Commits and code are in the git history as usual.

## PR #1: Annual budget plan: planned vs actual by category and month, with suggested budgets

Merged: 2026-10-23

Adds an **annual budget plan** to Securo: planned vs actual spending per category and month, plus a budget suggested from your spending history.

## What it does

**New page at `/budgets/plan`** (linked from the Budgets page and the command palette):
- Year summary (planned, spent so far, left in the plan) and a planned vs actual chart by month
- Category × month grid with three modes:
  - **Actual vs plan**: green when under, red when over
  - **Difference**: signed planned − actual
  - **Planned**: amounts edited inline; an empty cell removes that month's budget
- **Single month** view with a progress bar per category
- **Suggest budget**: proposes 12 months per category from the last 6, 12 or 24 months (median, average, or seasonal). The numbers can be edited before anything is saved, and months that already have a budget are kept unless "replace" is ticked.

**API** (`/api/budgets/year-plan`):
| Method | Path | Purpose |
|---|---|---|
| GET | `/year-plan?year=` | planned vs actual for every category and month |
| GET | `/year-plan/suggestion?year=&lookback_months=&method=` | proposed budget; read-only |
| PUT | `/year-plan` | bulk save of month-specific budgets (`amount: null` clears a month) |

Spending comes from the existing `get_budget_vs_actual`, so splits, FX, credit-card accounting mode and transfer exclusion match the Budgets page exactly.

## Design notes
- **Suggestions** use the median, so one expensive month doesn't inflate the plan. Categories with no spending in a third or more of the months use the average instead, because their median understates them.
- **Saved budgets** are ordinary month-specific budgets, so they also show up on the existing Budgets page. Clearing one lets a recurring default apply again.
- **The Year column** colours against the plan for months that have already happened, not the full-year plan, so a category over budget every month shows red before December.
- **Translations:** English and Brazilian Portuguese are translated. The other 16 locales carry English text, because the locale tests require every key in every file.

## Security review (OWASP Top 10)
- **Access control:** reads require workspace membership; writes require a write role (viewers get 403). Every category in a save is checked against the workspace, and unknown and foreign categories get the same error.
- **Injection:** ORM only, no raw SQL; no raw HTML on the frontend.
- **Input validation:** month 1–12, year 2000–2100, history 3–24 months, at most 6,000 cells per request, amounts from 0 up to the `NUMERIC(15,2)` limit. Not-a-number and infinity values are rejected.
- **Error handling:** a save race on the unique constraint returns 409 with nothing written, instead of a 500.
- **Known trade-off:** one year-plan request runs about 170 queries, and a 24-month suggestion about 360, because each month reuses the monthly calculation. That's acceptable for a self-hosted, logged-in app. A single dedicated summary query would be the fix if it ever feels slow.

## Tests
- **Backend:** 1,097 passed, including 33 new tests (service and API) and the full API contract sweep. The baseline adds only the 8 entries for the 3 new routes.
- **Frontend:** 1,085 passed across 132 files, including 13 new utility tests and 12 page tests rendered with a mocked API. Typecheck and lint are clean.
- **Visual check:** the page was rendered in Chromium with mock data in light mode, dark mode and at phone width, with no page-level horizontal scroll on a phone.
- **PostgreSQL:** not run locally (SQLite only); the PR's CI later ran the backend suite on PostgreSQL and passed.

## Commits
Each commit does one thing, so any piece can be reviewed or reverted on its own:
1. `feat(budgets)`: annual plan API
2. `test(budgets)`: backend tests and contract baseline
3. `feat(budgets)`: annual plan page
4. `i18n(budgets)`: strings
5. `fix(budgets)`: amount bound and 409 on a save race
6. `fix(budgets)`: failed inline edit snaps back

---

## PR #2: Sync with upstream: v0.16.6 + 61 commits (keeps the annual budget plan)

Merged: 2026-11-03

Brings the fork level with upstream **securo-finance/securo** `main` (`4226a3e`): the **v0.16.6** release plus 61 later commits, while keeping the annual budget plan.

## What comes in from upstream (highlights)
- **Budgets:** single-month limit suggestions, batch create, copy a month (with % adjustment), rollover summary, and a multi-month forecast
- **Loans:** contract terms, payment schedule, split mortgage payments
- **Reports and dashboard:** cash-flow net bars, year picker and month presets, balance comparison against chosen months
- **MCP and agents:** newer MCP protocol, more proposal tools, built-in agent templates
- **Fixes:** groups balances, transfers, SimpleFIN and Enable Banking sync, credit-card bills, asset precision, and nginx attachments over 1 MB
- **Compose:** workers now wait for migrations before starting (#1084)

## How the conflicts were resolved
Every conflict came from both sides adding code in the same place (imports, routes, schemas, client methods, types, Budgets page imports), and each was resolved by keeping both sides. Checked file by file: **no upstream line was dropped**. The only lines that differ from upstream are import lists extended with the annual-plan names, and the header wrapper on the Budgets page.

Upstream's new **Suggest** (one month, on the Budgets page) and the annual plan's **Suggest budget** (whole year, on `/budgets/plan`) are separate features with separate routes, and both remain available.

API contract baseline: upstream's 1,082 entries unchanged, plus the 8 annual-plan entries.

## Verification (local)
- Backend: **6,016 passed** (full suite), ruff clean
- Frontend: typecheck and lint clean; full test suite results in the comments
- CI on this PR runs the backend against PostgreSQL

## After merging
Publish a new release (e.g. `v0.16.7-plan.1`) to build fresh images.

---

## PR #3: Budgets menu opens the annual plan first

Merged: 2026-11-03

Clicking **Budgets** in the menu now opens the **annual plan** first.

- `/budgets` → annual plan; the month-by-month screen moves to `/budgets/monthly`
- `/budgets/plan` redirects to `/budgets`, so old links and bookmarks still work
- Each screen links to the other: **Monthly budgets** ↔ **Annual plan**
- Command palette: "Budgets" opens the plan, a new "Monthly budgets" entry, and "New budget" goes to the monthly screen
- The sidebar item is unchanged and stays highlighted on both screens

Tests: typecheck and lint clean; 524 affected frontend tests pass locally, including new link tests on both pages.

---

From here on the entries are short summaries; the full write-ups are in the pull requests.

## PR #4: Rule action "Transfer to account" and CDI yield for manual savings accounts

Merged: 2026-11-03

- **Rule action:** a rule can pair a matching transaction with a counterpart in a manual account, like the "create counterpart" button. Runs on sync, import and "apply to existing"; never targets a synced account. The stored action name is `transfer_to_account`.
- **CDI yield:** a manual account can earn a percentage of CDI. One "Rendimento CDI" credit is posted per day from Banco Central's rate (SGS series 12), on business days or every day. Settings live in `app_settings` under `cdi_yield:<account id>`, so there is no migration.

## PRs #5, #6, #7: Syncs with upstream v0.17.0 and the commits after it

Merged: 2026-11-04 to 2026-11-06. No conflicts, no migrations.

## PR #8: Recurring items from transactions, rule linking, and average amounts

Merged: 2026-11-07

- **Make recurring** from any transaction, synced ones included; the transaction becomes the first payment.
- **Rule action "Link to recurring"** (`link_recurring`): a rule decides which payments belong to a recurring item, so payments with changing amounts or descriptions still link. Linking removes the generated placeholder and moves the schedule on.
- **Amount follows the average** of the last 3 linked payments, refreshed hourly; the amount field is read-only while it is on. Settings live in `app_settings` under `recurring_amount:<recurring id>`.
- Deleting a recurring item removes the link actions that point at it.

## PR #9: Month outlook on the dashboard

Merged: 2026-11-07

"Will this month be covered?": money in cash accounts now, plus income still expected, minus bills and card bills still due, with the lowest point of the month. Unpaid bills already due this month count as overdue. Investment accounts are not cash. Card installments on later bills are shown apart. Follows the dashboard's Collection filter. Endpoint: `GET /api/month-outlook`.

## PR #10: CDI yield fixes, plus translations

Merged: 2026-11-07

- CDI: movements in another currency are converted to the account's currency; a rebuild removes the transfer legs rules made from yield rows and keeps the user's edits on them.
- The strings of #1, #3 and #4 translated in every locale.

## PR #11: Rule action renamed to "Mark as transfer with account"

Merged: 2026-11-07

The label is direction-neutral and a hint explains which way the money goes: for incoming money the counterpart is a debit in the chosen account. The stored action name is unchanged.

## PRs #12, #13: Sync with upstream v0.18.0, and the fork's features made to work with it

Merged: 2026-11-07. Migrations 103 to 106 (upstream).

- Upstream brought category splits and scoped access tokens.
- Linking keeps a split payment split, a payment linked to a split recurring item takes its parts, and "Make recurring" from a split payment keeps the split.
- Access tokens reach `/api/recurring-links` under the `recurring` scope and `/api/month-outlook` under `reports`.

## PR #14: Sync with upstream after v0.18.0 (removable rule packs)

Merged: 2026-11-09. Migration 107 (upstream).

Removing a rule pack deletes only untouched pack rules; rules made by the fork, and pack rules extended with a fork action, are kept. `.gitignore` gains `/data/attachments/`.

## PR #15: Review hardening

- **CDI rates** that can't be a daily rate (not finite, negative, or above 1% a day) are ignored instead of being posted as yield.
- **Access tokens:** "Make recurring" with a linking rule needs the `rules:write` scope as well as `recurring:write`.
- **Bank duplicates:** a synced row with an already-paired twin (same account, amount and direction, within a day, near-identical description) is not transferred by a rule, so the sync's duplicate cleanup still sees it and the manual account is not credited twice. A real second transfer of the same amount on the same day has to be paired by hand.
- **CDI rebuilds** remove the files attached to the yield rows they delete.
- **Rule names** made by "Make recurring" follow the user's language.

## PR #16: Annual plan shows income and uncategorized money

No migration.

- **Spending without a category** is counted in the plan's totals, as a read-only "Uncategorized" row (it can't have a budget).
- **Income section** above the spending grid: one row per income category, plus money received without a category. A category counts as income if it sits in the default "Income" group, has more money in than out over the year, or has a recurring income item. Income categories never count as spending.
- **Planned income** comes from active recurring income items (credits), month by month, with split items shared by their parts.
- **Net** per month and for the year: income received minus everything spent, as a row and a summary card.

## Conventions

- **Versions** follow upstream. Fork releases are tagged `v<upstream version>-plan.<N>`.
- **Translations:** new fork strings are written in English and Portuguese (pt-BR, pt-PT). Other locales carry the English text, because the locale tests require every key in every file.
