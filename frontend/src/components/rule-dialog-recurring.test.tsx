import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { RuleDialog } from '@/components/rule-dialog'
import { renderWithProviders } from '@/test/utils'

const recurringList = vi.hoisted(() => vi.fn(async () => [
  { id: 'r-mid', description: 'SALARIO', is_active: true },
  { id: 'r-end', description: 'PAGTO SALARIO', is_active: true },
]))

vi.mock('@/lib/api', () => ({
  rules: {
    preview: vi.fn(async () => ({ matched: 0, will_change: 0, will_apply: true, sample: [], offset: 0 })),
  },
  recurring: { list: recurringList },
  categories: { list: async () => [] },
  categoryGroups: { list: async () => [] },
  timezones: { list: async () => ({ default: 'UTC', available: [] }) },
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

function renderDialog(recurringItems?: { id: string; description: string }[]) {
  return renderWithProviders(
    <RuleDialog
      open
      onClose={() => {}}
      rule={null}
      categories={[]}
      categoryGroups={[]}
      accounts={[]}
      payees={[]}
      onSave={vi.fn()}
      loading={false}
      initialData={{ actions: [{ op: 'link_recurring', value: '' }] }}
      recurringItems={recurringItems}
    />,
  )
}

describe('RuleDialog "Link to recurring" choices', () => {
  it('loads the recurring items itself when opened from a transaction', async () => {
    renderDialog()
    expect(await screen.findByRole('option', { name: 'SALARIO' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'PAGTO SALARIO' })).toBeInTheDocument()
  })

  it('uses the list it is given without loading it again', async () => {
    recurringList.mockClear()
    renderDialog([{ id: 'r-given', description: 'Rent' }])
    expect(screen.getByRole('option', { name: 'Rent' })).toBeInTheDocument()
    expect(recurringList).not.toHaveBeenCalled()
  })
})
