// API clients for the fork's own features, on upstream's axios instance.
// Kept out of upstream's lib/api.ts so upstream merges don't touch it.
import { api } from '@/lib/api'
import type { RecurringTransaction } from '@/types'
import type {
  BudgetPlanCellWrite,
  BudgetSuggestionMethod,
  BudgetYearPlan,
  BudgetYearSuggestion,
  CdiYieldMode,
  CdiYieldSettings,
  MonthOutlook,
  MonthOutlookCardBill,
  RecurringAmountMode,
  RecurringFromTransactionInput,
} from '@/fork/types'

// CDI yield on a manual savings account. The API sends Decimals as strings.
function toCdiSettings(data: Record<string, unknown>): CdiYieldSettings {
  return {
    ...(data as unknown as CdiYieldSettings),
    percent: data.percent == null ? null : Number(data.percent),
    total_posted: Number(data.total_posted ?? 0),
  }
}

export const cdiYield = {
  get: async (accountId: string): Promise<CdiYieldSettings> => {
    const { data } = await api.get(`/accounts/${accountId}/cdi-yield`)
    return toCdiSettings(data)
  },
  save: async (
    accountId: string,
    settings: { percent: number; mode: CdiYieldMode; start_date: string },
  ): Promise<{ posted_days: number; settings: CdiYieldSettings }> => {
    const { data } = await api.put(`/accounts/${accountId}/cdi-yield`, { ...settings, enabled: true })
    return { posted_days: data.posted_days, settings: toCdiSettings(data.settings) }
  },
  recalculate: async (accountId: string): Promise<{ posted_days: number; settings: CdiYieldSettings }> => {
    const { data } = await api.post(`/accounts/${accountId}/cdi-yield/recalculate`)
    return { posted_days: data.posted_days, settings: toCdiSettings(data.settings) }
  },
  remove: async (accountId: string, deletePosted: boolean): Promise<void> => {
    await api.delete(`/accounts/${accountId}/cdi-yield`, { params: { delete_posted: deletePosted } })
  },
}

// Recurring items made from a transaction, and amounts that follow payments.
export const recurringLinks = {
  fromTransaction: async (
    transactionId: string,
    body: RecurringFromTransactionInput,
  ): Promise<{ recurring: RecurringTransaction; linked_history: number }> => {
    const { data } = await api.post(`/recurring-links/from-transaction/${transactionId}`, body)
    return { recurring: { ...data.recurring, amount: Number(data.recurring.amount) }, linked_history: data.linked_history }
  },
  amountModes: async (): Promise<RecurringAmountMode[]> => {
    const { data } = await api.get('/recurring-links/amount-modes')
    return data
  },
  setAmountMode: async (recurringId: string, followAverage: boolean): Promise<RecurringTransaction> => {
    const { data } = await api.put(`/recurring-links/${recurringId}/amount-mode`, { follow_average: followAverage })
    return { ...data, amount: Number(data.amount) }
  },
}

// Month outlook: money now, what comes in and goes out, what is left.
const OUTLOOK_NUMBERS = [
  'cash_now', 'start_balance', 'income_expected', 'bills_expected', 'card_bills',
  'end_balance', 'lowest_balance', 'committed_later',
] as const

export const monthOutlook = {
  get: async (month: string, accountIds?: string[]): Promise<MonthOutlook> => {
    const { data } = await api.get('/month-outlook', { params: { month, account_ids: accountIds } })
    const out = { ...data } as Record<string, unknown>
    for (const key of OUTLOOK_NUMBERS) out[key] = Number(data[key])
    out.events = (data.events ?? []).map((e: Record<string, unknown>) => ({ ...e, amount: Number(e.amount) }))
    out.card_bill_details = (data.card_bill_details ?? []).map((b: Record<string, unknown>) => ({
      ...b, amount: Number(b.amount), total: Number(b.total), paid: Number(b.paid),
    }))
    out.cash_accounts = (data.cash_accounts ?? []).map((a: Record<string, unknown>) => ({ ...a, balance: Number(a.balance) }))
    return out as unknown as MonthOutlook
  },
  /** Each credit card's next bill with something still to pay. */
  cardBills: async (): Promise<MonthOutlookCardBill[]> => {
    const { data } = await api.get('/month-outlook/card-bills')
    return (data ?? []).map((b: Record<string, unknown>) => ({
      ...b, amount: Number(b.amount), total: Number(b.total), paid: Number(b.paid),
    })) as MonthOutlookCardBill[]
  },
}

// Annual budget plan: /api/budgets/year-plan.
export const budgetPlan = {
  // The API serialises Decimals as strings ("250.50"); the plan screens do
  // arithmetic on them, so convert once here.
  yearPlan: async (year: number): Promise<BudgetYearPlan> => {
    const { data } = await api.get('/budgets/year-plan', { params: { year } })
    const num = (v: unknown) => Number(v)
    const numOrNull = (v: unknown) => (v == null ? null : Number(v))
    const months = (v: unknown) => (Array.isArray(v) ? v.map(num) : Array(12).fill(0))
    const row = (r: BudgetYearPlan['rows'][number]) => ({
      ...r,
      planned_total: num(r.planned_total),
      actual_total: num(r.actual_total),
      cells: r.cells.map((c) => ({ ...c, planned: numOrNull(c.planned), actual: num(c.actual) })),
    })
    // An older backend has no balance fields: fall back to income minus spending.
    const netActual: number[] = Array.isArray(data.net_actual_by_month)
      ? data.net_actual_by_month.map(num)
      : months(data.income_actual_by_month).map((v: number, i: number) => v - num(data.actual_by_month[i]))
    const netPlanned: number[] = Array.isArray(data.net_planned_by_month)
      ? data.net_planned_by_month.map(num)
      : months(data.income_planned_by_month).map((v: number, i: number) => v - num(data.planned_by_month[i]))
    const total = (values: number[]) => values.reduce((a, b) => a + b, 0)
    // An older backend has no moved money: the account balance is the balance.
    const moved: number[] = months(data.moved_by_month)
    const cashNet: number[] = Array.isArray(data.cash_net_by_month)
      ? data.cash_net_by_month.map(num)
      : netActual.map((v, i) => v - moved[i])
    return {
      ...data,
      moved_rows: (data.moved_rows ?? []).map(row),
      start_money: num(data.start_money ?? 0),
      month_end_money: Array.isArray(data.month_end_money)
        ? data.month_end_money.map(numOrNull)
        : Array(12).fill(null),
      moved_by_month: moved,
      moved_total: total(moved),
      cash_net_by_month: cashNet,
      cash_net_total: total(cashNet),
      net_actual_by_month: netActual,
      net_planned_by_month: netPlanned,
      net_actual_total: total(netActual),
      net_planned_total: total(netPlanned),
      planned_total: num(data.planned_total),
      actual_total: num(data.actual_total),
      planned_by_month: data.planned_by_month.map(num),
      actual_by_month: data.actual_by_month.map(num),
      rows: data.rows.map(row),
      uncategorized_by_month: months(data.uncategorized_by_month),
      uncategorized_total: num(data.uncategorized_total ?? 0),
      income_rows: (data.income_rows ?? []).map(row),
      income_planned_by_month: months(data.income_planned_by_month),
      income_actual_by_month: months(data.income_actual_by_month),
      income_planned_total: num(data.income_planned_total ?? 0),
      income_actual_total: num(data.income_actual_total ?? 0),
      uncategorized_income_by_month: months(data.uncategorized_income_by_month),
      uncategorized_income_total: num(data.uncategorized_income_total ?? 0),
    }
  },
  yearSuggestion: async (
    year: number,
    lookbackMonths: number,
    method: BudgetSuggestionMethod,
  ): Promise<BudgetYearSuggestion> => {
    const { data } = await api.get('/budgets/year-plan/suggestion', {
      params: { year, lookback_months: lookbackMonths, method },
    })
    return {
      ...data,
      rows: data.rows.map((r: BudgetYearSuggestion['rows'][number]) => ({
        ...r,
        history_average: Number(r.history_average),
        history_median: Number(r.history_median),
        proposed: r.proposed.map(Number),
        existing: r.existing.map((v) => (v == null ? null : Number(v))),
      })),
    }
  },
  applyYearPlan: async (
    year: number,
    cells: BudgetPlanCellWrite[],
  ): Promise<{ saved: number; deleted: number }> => {
    const { data } = await api.put('/budgets/year-plan', { year, cells })
    return data
  },
}
