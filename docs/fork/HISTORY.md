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

- **Spending without a category** is counted in the plan's totals, as a read-only "Uncategorized" row (it can't have a budget). For an expense shared in a group, only your share counts, as on the Budgets page.
- **Income section** above the spending grid: one row per income category, plus money received without a category. A category counts as income if it sits in the default "Income" group, has more money in than out over the year, or has a recurring income item. Income categories never count as spending. A category with a budget this year is not moved to income just because a refund outweighed its spending.
- **Planned income** comes from active recurring income items (credits), month by month, with split items shared by their parts.
- **Balance** per month: the real money in minus money out (a refund larger than a category's spending counts), with the planned balance under it (planned income minus budgets).
- **Accumulated balance:** the balance added up since January against where the plan says it should be, with the plan for the rest of the year shown ahead. The summary card shows the balance so far and the plan to date.

## PR #17: Sidebar accounts grouped, cards with their bill date

The sidebar's account list keeps the total in its header and splits the accounts in two groups, each with a subtotal: account balances first (checking, savings, investments), then credit cards ordered by the next bill, each showing its due date ("No due date set" when the card has no due day). The list always shows every account; the old "show more" cut at three is gone. New component `frontend/src/components/sidebar-account-groups.tsx`; `app-layout.tsx` only renders it.

## PR #20: Sidebar cards show the bill to pay, over the card total

Each credit card in the sidebar now leads with its next bill still to pay, with the card's total under it and the due date under its name. The cards' subtotal adds up those bills. A bill past its due date and still unpaid comes first, in red ("Vencida em …"), for up to 25 days. A card with nothing to pay shows its total and "Sem fatura a pagar".

- New route `GET /api/month-outlook/card-bills` (read-only, `reports:read` like the rest of `/api/month-outlook`), built on the month outlook's card-bill logic: the bank's bill when there is one, otherwise the card's charges plus recurring charges still to come, minus payments. `month_outlook_service.get_next_card_bills` and `_overdue_card_bills`; `CardBill` and `CardBillRead` gain `overdue`.
- API contract baseline: 2 new entries (fork total 39).
- The sidebar fetches it under the `['accounts', 'card-bills']` query key, so it refreshes whenever the account list does.

## PR #21: Sidebar header adds up its groups; overdue bills checked against the card's debt

- The sidebar header is now account balances plus the cards' next bills, so it matches the two groups below it (`frontend/src/lib/sidebar-account-totals.ts`). Each card's full debt still shows on its row as "Total".
- A past bill counts as overdue only for the part of the card's debt today that later charges don't explain (`_owed_on_card`, `_charged_after`). A payment recorded as a plain credit (common on bank-synced cards) now settles the bill, and a partial payment leaves only the rest overdue.

## PR #22: "Link to recurring" lists the items wherever a rule is created

The rule dialog only listed recurring items when the Rules page passed them in. Opened with "Create Rule" from a transaction, the transactions page or the dashboard, the "Link to recurring" picker said "No recurring items yet". The dialog now loads them itself when none are passed (query key `['recurring']`, shared with the Rules page). Test: `frontend/src/components/rule-dialog-recurring.test.tsx`.

## PR #23: Annual plan actuals open their transactions

In the annual plan, each actual amount (spending grid, income section, single-month view, and the Year column) is a link to the Transactions page filtered to that category, that month (or the year) and posted transactions; the "Uncategorized" rows open the uncategorized expenses or income. The list filters by the same reporting date the plan uses, so card purchases show in their bill month in both. Frontend only (`transactionsHref` and `ActualAmount` in `budget-plan.tsx`).

## PR #24: Annual plan shows money moved to investments, a summary block and totals-only view

- New "Investimentos e transferências" table: per transfer-like category (`treat_as_transfer`), debits minus credits of rows that are not paired with another tracked account (`_moved_rows`): money put into investments or sent to banks Securo does not track; negative when more came back. Paired transfers between tracked accounts are left out.
- New "Resumo" block at the bottom: Receitas, Gastos, Investido / transferido, Saldo em conta (`cash_net_by_month` = balance minus moved, so it matches what changed in the accounts) and Saldo acumulado. The balance rows moved here from the income table; the "Saldo até agora" card uses the account balance too.
- Each table collapses to its Total row; "Só totais" collapses all at once. The choice is remembered in the browser.

## PR #25: Money at month end in the plan; month outlook fixes

- Annual plan: "Saldo acumulado" is replaced by "Dinheiro no fim do mês": what the accounts really held on the last day of each month (today for the current month), balances minus card and loan debt, taken from the net-worth snapshot (`_money_at_month_ends`, reusing `report_service._net_worth_at` without assets). Under it, the plan: the money on Dec 31 of the previous year plus the planned balances. New fields `start_money` and `month_end_money`.
- Rows marked "Exclude from reports" no longer count as moved money (the old Buffer moves without a Cofrinho side).
- Month outlook (and the sidebar card bills): charges whose due date was computed from the card's settings, within 10 days of the bank's bill, belong to that bill (posted charges are already in the bank's total; recurring charges still to come are added to it). Without a bank bill, groups a few days apart are joined on the earliest. Fixes a card showing two or three bills in one month when the due day in Securo differs from the bank's.
- Month outlook: a pending payment on a bank-connected account dated today or earlier was assumed to be in the bank's balance. That stays true, except for a pending payment linked to a recurring item: the link takes the occurrence off the projection, so the pending row now counts as still to come (a salary the bank lists as pending before it lands).

## PR #26: Undo a recurring link made by mistake

- Linking a payment moves its recurring item past the occurrence it stands for, and upstream's unlink left the item where it was, while the edit form never moves the next date back. A wrong link (a mid-month salary linked to the day-30 one) hid that month's occurrence from the outlook with no way back.
- Unlink now steps the item back one period when that payment is what moved it: dated within one period before the occurrence, no other linked payment covering it, and not an old month (`recurring_link_service.step_back_after_unlink`).
- The recurring edit form has a "Next date" field (`next_occurrence` on the update schema). It can go back one period at most and not to an occurrence a linked payment already covers (`check_next_occurrence`); later dates are always allowed. A refused date shows the server's reason.

## Conventions

- **Agent instructions:** `CLAUDE.md` (repository root, fork-only) holds the facts a session needs: stack, commands, where each fork feature lives, what not to touch. The effort-level reminder, code-quality process and OWASP checklist are in `docs/fork/CONTRIBUTING.md`.

- **Versions** follow upstream. Fork releases are tagged `v<upstream version>-plan.<N>`.
- **Translations:** new fork strings are written in English and Portuguese (pt-BR, pt-PT). Other locales carry the English text, because the locale tests require every key in every file.
