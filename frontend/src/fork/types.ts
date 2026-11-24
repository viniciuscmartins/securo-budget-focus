// Types for the fork's own features. Kept out of upstream's types/index.ts so
// upstream merges don't touch it (docs/fork/REFACTOR-PLAN.md).
import type { RecurringTransaction } from '@/types'

export interface RecurringFromTransactionInput {
  frequency: RecurringTransaction['frequency']
  description?: string
  day_of_month?: number
  category_id?: string | null
  /** Expected amount follows the average of the last three payments. */
  follow_average: boolean
  /** Also create a rule so later payments link themselves. */
  link_rule?: { field: 'description' | 'payee' | 'notes'; op: 'contains' | 'equals' | 'starts_with'; value: string; name?: string } | null
}

export interface RecurringAmountMode {
  recurring_id: string
  mode: 'average'
  window: number
}

export interface BudgetYearCell {
  month: number // 1..12
  planned: number | null // null = no budget set
  actual: number
}

export interface BudgetYearRow {
  category_id: string
  category_name: string
  category_icon: string
  category_color: string
  group_id: string | null
  group_name: string | null
  cells: BudgetYearCell[] // 12 entries, January first
  planned_total: number
  actual_total: number
}

export interface BudgetYearPlan {
  year: number
  rows: BudgetYearRow[]
  planned_by_month: number[]
  actual_by_month: number[]
  planned_total: number
  actual_total: number
  /** Spending with no category (already included in actual_by_month). */
  uncategorized_by_month: number[]
  uncategorized_total: number
  /** Income categories: planned from recurring income items, actual received. */
  income_rows: BudgetYearRow[]
  income_planned_by_month: number[]
  income_actual_by_month: number[]
  income_planned_total: number
  income_actual_total: number
  /** Balance (money in minus money out): the real one, and the planned one. */
  net_actual_by_month: number[]
  net_planned_by_month: number[]
  net_actual_total: number
  net_planned_total: number
  /** Money moved through transfer-like categories (investments, transfers to
   * banks Securo does not track): debits minus credits, not spending. */
  moved_rows: BudgetYearRow[]
  moved_by_month: number[]
  moved_total: number
  /** What the accounts held (balances minus card and loan debt) when the year
   * started, and at each month end; null for months ahead. */
  start_money: number
  month_end_money: (number | null)[]
  /** What changed in the accounts: the balance minus what moved. */
  cash_net_by_month: number[]
  cash_net_total: number
  /** Money received with no category (already included in income_actual_by_month). */
  uncategorized_income_by_month: number[]
  uncategorized_income_total: number
}

export type BudgetSuggestionMethod = 'median' | 'average' | 'seasonal'

export interface BudgetSuggestionRow {
  category_id: string
  category_name: string
  category_icon: string
  category_color: string
  group_id: string | null
  group_name: string | null
  history_average: number
  history_median: number
  proposed: number[] // 12 entries
  existing: (number | null)[] // 12 entries
}

export interface BudgetYearSuggestion {
  year: number
  method: BudgetSuggestionMethod
  lookback_months: number
  history_start: string // YYYY-MM-DD, first day of oldest month used
  history_end: string // YYYY-MM-DD, first day of newest month used
  rows: BudgetSuggestionRow[]
}

export interface BudgetPlanCellWrite {
  category_id: string
  month: number
  amount: number | null // null clears the month-specific budget
}

export type CdiYieldMode = 'business_days' | 'calendar_days'

export interface CdiYieldSettings {
  configured: boolean
  enabled: boolean
  percent: number | null // 100 = 100% of CDI
  mode: CdiYieldMode | null
  start_date: string | null // YYYY-MM-DD
  tax_percent: number // income tax withheld from each day's yield; 0 = gross
  last_posted_date: string | null
  total_posted: number
}

export interface MonthOutlookEvent {
  date: string
  kind: 'income' | 'bill' | 'card_bill' | 'pending'
  description: string
  /** Signed: + comes in, - goes out. */
  amount: number
  account_id: string | null
  account_name: string | null
  /** Was due earlier this month (or today) and is still unpaid. */
  overdue?: boolean
}

export interface MonthOutlookCardBill {
  account_id: string
  account_name: string
  due_date: string
  amount: number
  total: number
  paid: number
  source: 'bank' | 'computed'
  /** Past its due date and still unpaid. */
  overdue?: boolean
}

export interface MonthOutlook {
  month: string
  currency: string
  today: string
  cash_now: number
  start_balance: number
  income_expected: number
  bills_expected: number
  card_bills: number
  end_balance: number
  lowest_balance: number
  lowest_date: string
  committed_later: number
  events: MonthOutlookEvent[]
  card_bill_details: MonthOutlookCardBill[]
  cash_accounts: { account_id: string; account_name: string; balance: number }[]
}
