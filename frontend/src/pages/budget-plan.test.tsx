import { beforeEach, describe, expect, it, vi } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'

import BudgetPlanPage from '@/pages/budget-plan'
import { renderWithProviders } from '@/test/utils'
import type { BudgetYearPlan, BudgetYearSuggestion } from '@/types'

const api = vi.hoisted(() => ({
  yearPlan: vi.fn(),
  yearSuggestion: vi.fn(),
  applyYearPlan: vi.fn(),
}))
vi.mock('@/lib/api', () => ({ budgets: api }))
vi.mock('@/contexts/auth-context', () => ({
  useAuth: () => ({ user: { preferences: { currency_display: 'USD' } } }),
}))
const workspace = vi.hoisted(() => ({ canWrite: true }))
vi.mock('@/contexts/workspace-context', () => ({ useWorkspace: () => workspace }))

// The page opens on the current year; pin "now" to late December so every
// month in the fixture has happened and nothing is greyed out as future.
const NOW = new Date(2026, 11, 20)
const YEAR = 2026

function cells(planned: (number | null)[], actual: number[]) {
  return planned.map((p, i) => ({ month: i + 1, planned: p, actual: actual[i] }))
}

function plan(): BudgetYearPlan {
  const foodActual = Array(12).fill(400)
  foodActual[2] = 650 // March is over budget
  const funActual = Array(12).fill(0)
  funActual[4] = 80
  const spent = foodActual.map((v, i) => v + funActual[i])
  return {
    year: YEAR,
    net_actual_by_month: spent.map((v) => -v),
    net_planned_by_month: Array(12).fill(-500),
    net_actual_total: -5030,
    net_planned_total: -6000,
    moved_rows: [],
    moved_by_month: Array(12).fill(0),
    moved_total: 0,
    cash_net_by_month: spent.map((v) => -v),
    cash_net_total: -5030,
    rows: [
      {
        category_id: 'food',
        category_name: 'Food',
        category_icon: 'x',
        category_color: '#111111',
        group_id: null,
        group_name: null,
        cells: cells(Array(12).fill(500), foodActual),
        planned_total: 6000,
        actual_total: 4950,
      },
      {
        category_id: 'fun',
        category_name: 'Fun',
        category_icon: 'x',
        category_color: '#222222',
        group_id: null,
        group_name: null,
        cells: cells(Array(12).fill(null), funActual),
        planned_total: 0,
        actual_total: 80,
      },
    ],
    planned_by_month: Array(12).fill(500),
    actual_by_month: foodActual.map((v, i) => v + funActual[i]),
    planned_total: 6000,
    actual_total: 5030,
    uncategorized_by_month: Array(12).fill(0),
    uncategorized_total: 0,
    income_rows: [],
    income_planned_by_month: Array(12).fill(0),
    income_actual_by_month: Array(12).fill(0),
    income_planned_total: 0,
    income_actual_total: 0,
    uncategorized_income_by_month: Array(12).fill(0),
    uncategorized_income_total: 0,
  }
}

function suggestion(): BudgetYearSuggestion {
  const existing: (number | null)[] = Array(12).fill(null)
  existing[0] = 999
  return {
    year: YEAR,
    method: 'median',
    lookback_months: 12,
    history_start: '2025-12-01',
    history_end: '2026-11-01',
    rows: [
      {
        category_id: 'food',
        category_name: 'Food',
        category_icon: 'x',
        category_color: '#111111',
        group_id: null,
        group_name: null,
        history_average: 410,
        history_median: 400,
        proposed: Array(12).fill(400),
        existing,
      },
    ],
  }
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(NOW)
  vi.clearAllMocks()
  workspace.canWrite = true
  api.yearPlan.mockResolvedValue(plan())
  api.yearSuggestion.mockResolvedValue(suggestion())
  api.applyYearPlan.mockResolvedValue({ saved: 1, deleted: 0 })
})

async function renderPage() {
  const view = renderWithProviders(<BudgetPlanPage />)
  await screen.findByText('Food')
  return view
}

describe('BudgetPlanPage', () => {
  it('shows every category with planned vs actual and the year totals', async () => {
    await renderPage()
    expect(api.yearPlan).toHaveBeenCalledWith(YEAR)
    expect(screen.getByText('Fun')).toBeInTheDocument()
    expect(screen.getByText('Planned for the year').nextSibling).toHaveTextContent('$6,000')
    expect(screen.getByText('Spent so far').nextSibling).toHaveTextContent('$5,030')
    expect(screen.getByText('Left in the plan').nextSibling).toHaveTextContent('$970')
  })

  it('links to the month-by-month budgets screen', async () => {
    await renderPage()
    expect(screen.getByRole('link', { name: /Monthly budgets/ })).toHaveAttribute('href', '/budgets/monthly')
  })

  it('flags the month that went over budget and not the others', async () => {
    await renderPage()
    const foodRow = screen.getByText('Food').closest('tr')!
    expect(within(foodRow).getByText('$650').className).toContain('text-rose-500')
    expect(within(foodRow).getAllByText('$400')[0].className).toContain('text-emerald-600')
  })

  it('colours the Year column against the plan to date, not the full-year plan', async () => {
    // Over budget every month, Jan-Oct; Nov and Dec haven't happened yet.
    vi.setSystemTime(new Date(2026, 9, 20))
    const actual = [...Array(10).fill(110), 0, 0]
    api.yearPlan.mockResolvedValue({
      ...plan(),
      rows: [
        {
          ...plan().rows[0],
          category_id: 'over',
          category_name: 'Always over',
          cells: cells(Array(12).fill(100), actual),
          planned_total: 1200, // more than the 1100 spent so far...
          actual_total: 1100, // ...but 100 over the 1000 planned for Jan-Oct
        },
      ],
    })
    renderWithProviders(<BudgetPlanPage />)
    const row = (await screen.findByText('Always over')).closest('tr')!
    const yearCell = row.lastElementChild as HTMLElement
    expect(within(yearCell).getByText('$1,100').className).toContain('text-rose-500')
    expect(within(yearCell).getByText('$1,200')).toBeInTheDocument() // still shows the full-year plan
  })

  it('shows signed differences in the difference view', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('tab', { name: 'Difference' }))
    const foodRow = screen.getByText('Food').closest('tr')!
    expect(within(foodRow).getByText('-$150')).toBeInTheDocument() // March: 500 - 650
    expect(within(foodRow).getAllByText('+$100').length).toBe(11)
    // Year column: 11 * 100 - 150
    expect(within(foodRow).getByText('+$950')).toBeInTheDocument()
  })

  it('saves a single edited cell, and clears it when emptied', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('tab', { name: 'Planned' }))
    const jan = screen.getByLabelText('Planned amount for Food in Jan')
    await user.clear(jan)
    await user.type(jan, '650')
    await user.tab()
    await waitFor(() =>
      expect(api.applyYearPlan).toHaveBeenCalledWith(YEAR, [{ category_id: 'food', month: 1, amount: 650 }]),
    )

    const feb = screen.getByLabelText('Planned amount for Food in Feb')
    await user.clear(feb)
    await user.tab()
    await waitFor(() =>
      expect(api.applyYearPlan).toHaveBeenLastCalledWith(YEAR, [{ category_id: 'food', month: 2, amount: null }]),
    )
  })

  it('puts the saved amount back when a save fails', async () => {
    api.applyYearPlan.mockRejectedValue(new Error('network down'))
    const { user } = await renderPage()
    await user.click(screen.getByRole('tab', { name: 'Planned' }))
    await user.clear(screen.getByLabelText('Planned amount for Food in Jan'))
    await user.type(screen.getByLabelText('Planned amount for Food in Jan'), '999')
    await user.tab()
    await waitFor(() => expect(api.applyYearPlan).toHaveBeenCalledTimes(1))
    // The field re-mounts, so query it again.
    await waitFor(() => expect(screen.getByLabelText('Planned amount for Food in Jan')).toHaveValue(500))
  })

  it('does not save when the value is unchanged or invalid', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('tab', { name: 'Planned' }))
    const input = screen.getByLabelText('Planned amount for Food in Jan')
    await user.click(input)
    await user.tab() // untouched
    await user.clear(input)
    await user.type(input, '-5')
    await user.tab()
    expect(api.applyYearPlan).not.toHaveBeenCalled()
    expect(input).toHaveValue(500) // reverted to the saved amount
  })

  it('is read-only without write access', async () => {
    workspace.canWrite = false
    const { user } = await renderPage()
    expect(screen.queryByRole('button', { name: /Suggest budget/ })).not.toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Planned' }))
    expect(screen.queryByLabelText('Planned amount for Food in Jan')).not.toBeInTheDocument()
  })

  it('lists categories for one month in the month view', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('tab', { name: 'Single month' }))
    await user.selectOptions(screen.getByLabelText('Month'), '5')
    const fun = screen.getByText('Fun').closest('tr')!
    expect(within(fun).getByText('no budget')).toBeInTheDocument()
    expect(within(fun).getByText('$80')).toBeInTheDocument()
  })

  it('previews a suggestion, keeps existing months by default, and applies the reviewed cells', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: /Suggest budget/ }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByLabelText('Planned amount for Food in Feb')

    // January already has a budget: shown as kept, not editable; 11 months to save.
    expect(within(dialog).queryByLabelText('Planned amount for Food in Jan')).not.toBeInTheDocument()
    expect(within(dialog).getByText('Entries to save: 11')).toBeInTheDocument()

    const feb = within(dialog).getByLabelText('Planned amount for Food in Feb')
    await user.clear(feb)
    await user.type(feb, '450')
    await user.click(within(dialog).getByRole('button', { name: 'Apply budget' }))

    await waitFor(() => expect(api.applyYearPlan).toHaveBeenCalledTimes(1))
    const [, sent] = api.applyYearPlan.mock.calls[0]
    expect(sent).toHaveLength(11)
    expect(sent.find((c: { month: number }) => c.month === 1)).toBeUndefined()
    expect(sent.find((c: { month: number }) => c.month === 2).amount).toBe(450)
  })

  it('includes existing months once "replace" is ticked', async () => {
    const { user } = await renderPage()
    await user.click(screen.getByRole('button', { name: /Suggest budget/ }))
    const dialog = await screen.findByRole('dialog')
    await within(dialog).findByLabelText('Planned amount for Food in Feb')
    await user.click(within(dialog).getByLabelText('Replace months that already have a budget'))
    expect(within(dialog).getByText('Entries to save: 12')).toBeInTheDocument()
  })

  it('shows income against the plan from recurring items, and the net', async () => {
    const salaryActual = Array(12).fill(0)
    for (let m = 0; m < 10; m++) salaryActual[m] = 5000
    salaryActual[9] = 4500 // October came in short
    const base = plan()
    api.yearPlan.mockResolvedValue({
      ...base,
      net_actual_by_month: salaryActual.map((v, i) => v - base.actual_by_month[i]),
      net_planned_by_month: Array(12).fill(4500),
      net_actual_total: 44470,
      net_planned_total: 54000,
      cash_net_by_month: salaryActual.map((v, i) => v - base.actual_by_month[i]),
      cash_net_total: 44470,
      income_rows: [{
        category_id: 'salary', category_name: 'Salary', category_icon: 'x', category_color: '#16A34A',
        group_id: null, group_name: null,
        cells: cells(Array(12).fill(5000), salaryActual),
        planned_total: 60000, actual_total: 49500,
      }],
      income_planned_by_month: Array(12).fill(5000),
      income_actual_by_month: salaryActual,
      income_planned_total: 60000,
      income_actual_total: 49500,
    })
    await renderPage()
    const row = screen.getByText('Salary').closest('tr')!
    expect(within(row).getAllByText('$5,000')[0].className).toContain('text-emerald-600')
    expect(within(row).getByText('$4,500').className).toContain('text-amber-600')
    expect(screen.getByText('Income received').nextSibling).toHaveTextContent('$49,500')
    // The balance so far against the plan to date, on the card.
    const card = screen.getByText('Balance so far').parentElement!
    expect(card).toHaveTextContent('$44,470')
    expect(card).toHaveTextContent('Planned to date: $54,000')
    // Each month: the real balance over the planned one (5,000 - 500).
    const balance = screen.getByText('Balance in accounts').closest('tr')!
    expect(within(balance).getAllByText('$4,600')[0].className).toContain('text-emerald-600')
    expect(within(balance).getAllByText('$4,500').length).toBeGreaterThan(0)
    // December had no salary: money went out.
    expect(within(balance).getAllByText('-$400').at(-1)!.className).toContain('text-rose')
    // Added up through the year, against where the plan should be.
    const running = screen.getByText('Accumulated balance').closest('tr')!
    expect(within(running).getAllByText('$4,600')[0].className).toContain('text-emerald-600')
    expect(within(running).getAllByText('$44,470')[0].className).toContain('text-amber-600')
    expect(within(running).getAllByText('$54,000').length).toBeGreaterThan(0)
  })

  it('shows money moved to investments and the balance left in the accounts', async () => {
    const base = plan()
    const moved = Array(12).fill(0)
    moved[5] = 3000
    api.yearPlan.mockResolvedValue({
      ...base,
      moved_rows: [{
        category_id: 'invest', category_name: 'Investimentos', category_icon: 'x', category_color: '#0EA5E9',
        group_id: null, group_name: null, cells: cells(Array(12).fill(null), moved),
        planned_total: 0, actual_total: 3000,
      }],
      moved_by_month: moved,
      moved_total: 3000,
      cash_net_by_month: base.net_actual_by_month.map((v, i) => v - moved[i]),
      cash_net_total: base.net_actual_total - 3000,
    })
    await renderPage()
    expect(screen.getByText('Investments and transfers')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Investimentos, Jun: see transactions' })).toHaveTextContent('$3,000')
    // The summary: what went to investments, and what is left in the accounts.
    const movedRow = screen.getByText('Invested / transferred').closest('tr')!
    expect(movedRow).toHaveTextContent('$3,000')
    const balance = screen.getByText('Balance in accounts').closest('tr')!
    expect(balance).toHaveTextContent('-$3,400')  // June: -400 spent, 3,000 invested
    expect(screen.getByText('Balance so far').parentElement!).toHaveTextContent('-$8,030')
  })

  it('collapses the tables to their totals', async () => {
    const { user } = await renderPage()
    expect(screen.getByText('Food')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Only totals' }))
    expect(screen.queryByText('Food')).toBeNull()
    expect(screen.getAllByText('Total').length).toBeGreaterThan(1)
    expect(screen.getByText('Balance in accounts')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Only totals' }))
    expect(screen.getByText('Food')).toBeInTheDocument()
    // One table on its own.
    const spending = screen.getByRole('region', { name: 'Spending' })
    await user.click(within(spending).getByRole('button', { name: 'Hide categories' }))
    expect(screen.queryByText('Food')).toBeNull()
    await user.click(within(spending).getByRole('button', { name: 'Show categories' }))
    expect(screen.getByText('Food')).toBeInTheDocument()
  })

  it('links each actual to that month\'s transactions for the category', async () => {
    await renderPage()
    // Food, March: the posted transactions of that category in that month.
    const march = screen.getByRole('link', { name: 'Food, Mar: see transactions' })
    expect(march).toHaveTextContent('$650')
    expect(march).toHaveAttribute(
      'href',
      `/transactions?from=${YEAR}-03-01&to=${YEAR}-03-31&status=posted&category_id=food`,
    )
    // The year column covers the whole year.
    expect(screen.getByRole('link', { name: 'Food, Year: see transactions' })).toHaveAttribute(
      'href',
      `/transactions?from=${YEAR}-01-01&to=${YEAR}-12-31&status=posted&category_id=food`,
    )
    // A month with nothing spent has nothing to open.
    expect(screen.queryByRole('link', { name: 'Fun, Jan: see transactions' })).toBeNull()
  })

  it('links uncategorized spending to the uncategorized expenses of the month', async () => {
    const uncategorized = Array(12).fill(0)
    uncategorized[1] = 70
    api.yearPlan.mockResolvedValue({ ...plan(), uncategorized_by_month: uncategorized, uncategorized_total: 70 })
    await renderPage()
    expect(screen.getByRole('link', { name: 'Uncategorized, Feb: see transactions' })).toHaveAttribute(
      'href',
      `/transactions?from=${YEAR}-02-01&to=${YEAR}-02-28&status=posted&uncategorized=1&type=debit`,
    )
  })

  it('shows spending without a category as its own read-only row', async () => {
    workspace.canWrite = true
    const uncategorized = Array(12).fill(0)
    uncategorized[1] = 70
    api.yearPlan.mockResolvedValue({ ...plan(), uncategorized_by_month: uncategorized, uncategorized_total: 70 })
    const { user } = await renderPage()
    const row = screen.getByText('Uncategorized').closest('tr')!
    expect(within(row).getAllByText('$70').length).toBeGreaterThan(0)
    await user.click(screen.getByRole('tab', { name: 'Planned' }))
    expect(within(screen.getByText('Uncategorized').closest('tr')!).queryByRole('spinbutton')).toBeNull()
  })

  it('shows money received without a category under income', async () => {
    const received = Array(12).fill(0)
    received[0] = 5000
    api.yearPlan.mockResolvedValue({
      ...plan(),
      uncategorized_income_by_month: received,
      uncategorized_income_total: 5000,
      income_actual_by_month: received,
      income_actual_total: 5000,
    })
    await renderPage()
    expect(screen.queryByText(/No income yet/)).toBeNull()
    const rows = screen.getAllByText('Uncategorized').map((el) => el.closest('tr')!)
    expect(rows.some((row) => row.textContent?.includes('$5,000'))).toBe(true)
  })

  it('offers a suggestion when there is no data yet', async () => {
    api.yearPlan.mockResolvedValue({
      year: YEAR,
      rows: [],
      planned_by_month: Array(12).fill(0),
      actual_by_month: Array(12).fill(0),
      planned_total: 0,
      actual_total: 0,
      net_actual_by_month: Array(12).fill(0),
      net_planned_by_month: Array(12).fill(0),
      net_actual_total: 0,
      net_planned_total: 0,
      moved_rows: [],
      moved_by_month: Array(12).fill(0),
      moved_total: 0,
      cash_net_by_month: Array(12).fill(0),
      cash_net_total: 0,
      uncategorized_by_month: Array(12).fill(0),
      uncategorized_total: 0,
      income_rows: [],
      income_planned_by_month: Array(12).fill(0),
      income_actual_by_month: Array(12).fill(0),
      income_planned_total: 0,
      income_actual_total: 0,
      uncategorized_income_by_month: Array(12).fill(0),
      uncategorized_income_total: 0,
    })
    renderWithProviders(<BudgetPlanPage />)
    expect(await screen.findByText('No budgets or spending for this year yet.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Suggest budget/ })).toBeInTheDocument()
  })
})
