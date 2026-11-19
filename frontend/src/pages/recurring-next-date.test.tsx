import { fireEvent, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { renderWithProviders, t } from '@/test/utils'
import type { RecurringTransaction } from '@/types'

vi.mock('@/hooks/use-display-locale', () => ({
  useDateLocale: () => 'en-GB',
  useDisplayLocale: () => 'en-GB',
  useDisplayLocaleSettled: () => true,
}))
vi.mock('@/contexts/auth-context', () => ({ useAuth: () => ({ user: null }) }))
vi.mock('@/lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api')>()),
  currencies: { list: vi.fn().mockResolvedValue([]) },
}))
vi.mock('@/fork/api', () => ({
  recurringLinks: { amountModes: vi.fn().mockResolvedValue([]) },
}))
// A plain input stands in for the calendar popover, named by its placeholder.
vi.mock('@/components/ui/date-picker-input', () => ({
  DatePickerInput: ({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) => (
    <input aria-label={placeholder ?? 'date'} value={value} onChange={(e) => onChange(e.target.value)} />
  ),
}))

import { RecurringForm } from './recurring'

const salary = {
  id: 'rec-1',
  description: 'SALARIO DIA 30',
  amount: 7769,
  currency: 'BRL',
  type: 'credit',
  frequency: 'monthly',
  weekend_adjustment: 'none',
  day_of_month: 30,
  start_date: '2026-01-30',
  end_date: null,
  next_occurrence: '2026-12-30',
  category_id: null,
  account_id: 'acc-1',
  is_active: true,
  auto_generate: false,
} as unknown as RecurringTransaction

function renderForm(recurring: RecurringTransaction | null) {
  const onSave = vi.fn()
  renderWithProviders(
    <RecurringForm
      recurring={recurring}
      categories={[]}
      categoryGroups={[]}
      accounts={[{ id: 'acc-1', name: 'Checking' }]}
      onSave={onSave}
      onCancel={vi.fn()}
      loading={false}
    />,
  )
  return onSave
}

it('sends a next date set back by hand', () => {
  const onSave = renderForm(salary)
  const next = screen.getByLabelText(t('recurring.nextDate'))
  expect(next).toHaveValue('2026-12-30')

  fireEvent.change(next, { target: { value: '2026-11-30' } })
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ next_occurrence: '2026-11-30' }))
})

it('leaves the next date out when it was not changed', () => {
  const onSave = renderForm(salary)
  fireEvent.click(screen.getByRole('button', { name: t('common.save') }))

  expect(onSave).toHaveBeenCalled()
  expect(onSave.mock.calls[0][0]).not.toHaveProperty('next_occurrence')
})

it('has no next date when creating an item', () => {
  renderForm(null)
  expect(screen.queryByLabelText(t('recurring.nextDate'))).not.toBeInTheDocument()
})
