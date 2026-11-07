import { screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MakeRecurringPanel } from '@/components/recurring-link'
import { canMakeRecurring, suggestedMatch } from '@/lib/recurring-link-utils'
import { RuleDialog } from '@/components/rule-dialog'
import { renderWithProviders, t } from '@/test/utils'
import type { Transaction } from '@/types'

const api = vi.hoisted(() => ({
  fromTransaction: vi.fn(),
  amountModes: vi.fn(async () => []),
  setAmountMode: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  recurringLinks: api,
  rules: {
    preview: vi.fn(async () => ({ matched: 0, will_change: 0, will_apply: true, sample: [], offset: 0 })),
  },
  categories: { list: async () => [] },
  categoryGroups: { list: async () => [] },
  timezones: { list: async () => ({ default: 'UTC', available: [] }) },
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const tx = {
  id: 'tx-1',
  description: 'PIX ENVIADO ESCOLA ABC',
  payee: 'Escola ABC',
  date: '2026-10-07',
  amount: 1200,
  type: 'debit',
  source: 'sync',
  recurring_transaction_id: null,
  transfer_pair_id: null,
} as unknown as Transaction

beforeEach(() => {
  vi.clearAllMocks()
  api.fromTransaction.mockResolvedValue({ recurring: { id: 'rec-1' }, linked_history: 2 })
})

describe('helpers', () => {
  it('suggests the payee, else the description', () => {
    expect(suggestedMatch(tx)).toEqual({ field: 'payee', value: 'Escola ABC' })
    expect(suggestedMatch({ ...tx, payee: null })).toEqual({ field: 'description', value: 'PIX ENVIADO ESCOLA ABC' })
  })

  it('offers make-recurring only for real, unlinked, non-transfer rows', () => {
    expect(canMakeRecurring(tx)).toBe(true)
    expect(canMakeRecurring(null)).toBe(false)
    expect(canMakeRecurring({ ...tx, recurring_transaction_id: 'r' })).toBe(false)
    expect(canMakeRecurring({ ...tx, transfer_pair_id: 'p' })).toBe(false)
    expect(canMakeRecurring({ ...tx, source: 'recurring' })).toBe(false)
    expect(canMakeRecurring({ ...tx, source: 'opening_balance' })).toBe(false)
  })
})

describe('MakeRecurringPanel', () => {
  it('pre-fills from the transaction and creates the item with a linking rule', async () => {
    const onCreated = vi.fn()
    const { user } = renderWithProviders(<MakeRecurringPanel transaction={tx} onCreated={onCreated} />)

    await user.click(screen.getByRole('button', { name: t('recurringLink.makeRecurring') }))
    expect(screen.getByLabelText(t('recurring.description'))).toHaveValue('Escola ABC')
    expect(screen.getByLabelText(t('recurringLink.dayOfMonth'))).toHaveValue(7)
    expect(screen.getByLabelText(t('recurringLink.ruleValue'))).toHaveValue('Escola ABC')

    await user.click(screen.getByRole('button', { name: t('recurringLink.create') }))

    await waitFor(() => expect(api.fromTransaction).toHaveBeenCalledTimes(1))
    expect(api.fromTransaction).toHaveBeenCalledWith('tx-1', {
      frequency: 'monthly',
      description: 'Escola ABC',
      day_of_month: 7,
      follow_average: true,
      link_rule: { field: 'payee', op: 'contains', value: 'Escola ABC' },
    })
    await waitFor(() => expect(onCreated).toHaveBeenCalled())
  })

  it('can skip the rule and the average', async () => {
    const { user } = renderWithProviders(<MakeRecurringPanel transaction={tx} />)
    await user.click(screen.getByRole('button', { name: t('recurringLink.makeRecurring') }))
    await user.click(screen.getByLabelText(new RegExp(t('recurringLink.followAverage'))))
    await user.click(screen.getByLabelText(new RegExp(t('recurringLink.createRule'))))
    await user.selectOptions(screen.getByLabelText(t('recurring.frequency')), 'weekly')
    expect(screen.queryByLabelText(t('recurringLink.dayOfMonth'))).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('recurringLink.create') }))
    await waitFor(() => expect(api.fromTransaction).toHaveBeenCalled())
    const [, body] = api.fromTransaction.mock.calls[0]
    expect(body).toEqual({ frequency: 'weekly', description: 'Escola ABC', follow_average: false, link_rule: null })
  })

  it('will not create with an empty rule text or a bad day', async () => {
    const { user } = renderWithProviders(<MakeRecurringPanel transaction={tx} />)
    await user.click(screen.getByRole('button', { name: t('recurringLink.makeRecurring') }))
    const create = screen.getByRole('button', { name: t('recurringLink.create') })

    await user.clear(screen.getByLabelText(t('recurringLink.ruleValue')))
    expect(create).toBeDisabled()
    await user.type(screen.getByLabelText(t('recurringLink.ruleValue')), 'escola')
    await user.clear(screen.getByLabelText(t('recurringLink.dayOfMonth')))
    await user.type(screen.getByLabelText(t('recurringLink.dayOfMonth')), '40')
    expect(create).toBeDisabled()
  })
})

describe('RuleDialog "Link to recurring" action', () => {
  it('lists recurring items and saves the chosen one', async () => {
    const onSave = vi.fn()
    const { user } = renderWithProviders(
      <RuleDialog
        open
        onClose={() => {}}
        rule={null}
        categories={[]}
        categoryGroups={[]}
        accounts={[]}
        payees={[]}
        recurringItems={[{ id: 'rec-1', description: 'School' }, { id: 'rec-2', description: 'Salary' }]}
        onSave={onSave}
        loading={false}
      />,
    )
    const dialog = await screen.findByRole('dialog')
    const [name, conditionValue] = within(dialog).getAllByRole('textbox')
    await user.type(name, 'School link')
    await user.type(conditionValue, 'escola')
    const opSelect = within(dialog)
      .getAllByRole('combobox')
      .find((el) => within(el).queryByRole('option', { name: t('recurringLink.linkToRecurring') }) !== null)!
    await user.selectOptions(opSelect, 'link_recurring')

    const target = within(dialog).getByLabelText(t('recurringLink.linkToRecurring'))
    expect(within(target).getAllByRole('option').map((o) => o.textContent)).toEqual([
      t('recurringLink.selectRecurring'), 'School', 'Salary',
    ])
    await user.selectOptions(target, 'rec-1')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(onSave).toHaveBeenCalled())
    expect(onSave.mock.calls[0][0].actions).toContainEqual({ op: 'link_recurring', value: 'rec-1' })
  })
})
