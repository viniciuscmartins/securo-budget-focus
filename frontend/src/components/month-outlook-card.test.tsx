import { screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MonthOutlookCard } from '@/components/month-outlook-card'
import { renderWithProviders, t } from '@/test/utils'
import type { MonthOutlook } from '@/types'

const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@/lib/api', () => ({
  monthOutlook: api,
  timezones: { list: async () => ({ default: 'UTC', available: [] }) },
}))

const base: MonthOutlook = {
  month: '2026-11-01',
  currency: 'BRL',
  today: '2026-11-06',
  cash_now: 4000,
  start_balance: 4000,
  income_expected: 5000,
  bills_expected: 1200,
  card_bills: 800,
  end_balance: 7000,
  lowest_balance: 2000,
  lowest_date: '2026-11-15',
  committed_later: 200,
  events: [
    { date: '2026-11-10', kind: 'card_bill', description: 'Card', amount: -800, account_id: 'c', account_name: 'Card' },
    { date: '2026-11-25', kind: 'income', description: 'Salary', amount: 5000, account_id: 'a', account_name: 'Checking' },
  ],
  card_bill_details: [
    { account_id: 'c', account_name: 'Card', due_date: '2026-11-10', amount: 800, total: 800, paid: 0, source: 'computed' },
  ],
  cash_accounts: [],
}

function render(mask = (v: string) => v) {
  return renderWithProviders(<MonthOutlookCard month="2026-11" locale="en-US" dateLocale="en-US" mask={mask} />)
}

beforeEach(() => vi.clearAllMocks())

describe('MonthOutlookCard', () => {
  it('shows the end of month, the breakdown and that the month is covered', async () => {
    api.get.mockResolvedValue(base)
    render()
    expect(await screen.findByText(t('monthOutlook.covered'))).toBeInTheDocument()
    expect(api.get).toHaveBeenCalledWith('2026-11-01')
    expect(screen.getByText(/7,000\.00/)).toBeInTheDocument()
    expect(screen.getByText(t('monthOutlook.cashNow'))).toBeInTheDocument()
    expect(screen.getByText(t('monthOutlook.committedLaterHint'))).toBeInTheDocument()
  })

  it('warns when the balance dips below zero before income arrives', async () => {
    api.get.mockResolvedValue({ ...base, lowest_balance: -300 })
    render()
    expect(await screen.findByText(t('monthOutlook.dipsBelowZero', { date: 'Nov 15' }))).toBeInTheDocument()
  })

  it('says when the month is not covered', async () => {
    api.get.mockResolvedValue({ ...base, end_balance: -50, lowest_balance: -50 })
    render()
    expect(await screen.findByText(t('monthOutlook.notCovered'))).toBeInTheDocument()
  })

  it('lists card bills and the timeline on request', async () => {
    api.get.mockResolvedValue(base)
    const { user } = render()
    await user.click(await screen.findByRole('button', { name: t('monthOutlook.showDetails') }))
    expect(screen.getByText(t('monthOutlook.cardBillsTitle'))).toBeInTheDocument()
    expect(screen.getByText('Salary')).toBeInTheDocument()
  })

  it('a later month starts from the money at the start of the month', async () => {
    api.get.mockResolvedValue({ ...base, month: '2026-12-01' })
    render()
    expect(await screen.findByText(t('monthOutlook.startBalance'))).toBeInTheDocument()
  })

  it('hides amounts in privacy mode', async () => {
    api.get.mockResolvedValue(base)
    render(() => '•••')
    await screen.findByText(t('monthOutlook.covered'))
    expect(screen.queryByText(/7,000/)).not.toBeInTheDocument()
  })
})
