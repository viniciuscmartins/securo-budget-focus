import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { RuleDialog } from '@/components/rule-dialog'
import { renderWithProviders, t } from '@/test/utils'

vi.mock('@/lib/api', () => ({
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

const accounts = [
  { id: 'cc', name: 'Mercado Pago - CC', connection_id: 'conn-1', is_closed: false },
  { id: 'cofrinho', name: 'Mercado Pago - Cofrinho', connection_id: null, is_closed: false },
  { id: 'old', name: 'Old piggy bank', connection_id: null, is_closed: true },
]

function renderDialog(onSave = vi.fn()) {
  const view = renderWithProviders(
    <RuleDialog
      open
      onClose={() => {}}
      rule={null}
      categories={[]}
      categoryGroups={[]}
      accounts={accounts}
      payees={[]}
      onSave={onSave}
      loading={false}
    />,
  )
  return { onSave, user: view.user }
}

async function chooseTransferAction(user: ReturnType<typeof renderDialog>['user'], dialog: HTMLElement) {
  const opSelect = within(dialog)
    .getAllByRole('combobox')
    .find((el) => within(el).queryByRole('option', { name: t('rules.transferToAccount') }) !== null)!
  await user.selectOptions(opSelect, 'transfer_to_account')
}

describe('RuleDialog "Mark as transfer with account" action', () => {
  it('offers only manual, open accounts as the destination', async () => {
    const { user } = renderDialog()
    const dialog = await screen.findByRole('dialog')
    await chooseTransferAction(user, dialog)

    const target = within(dialog).getByLabelText(t('rules.transferToAccount'))
    const names = within(target).getAllByRole('option').map((o) => o.textContent)
    expect(names).toEqual([t('rules.selectManualAccount'), 'Mercado Pago - Cofrinho'])
  })

  it('explains that the direction follows the matched transaction', async () => {
    const { user } = renderDialog()
    const dialog = await screen.findByRole('dialog')
    await chooseTransferAction(user, dialog)

    const target = within(dialog).getByLabelText(t('rules.transferToAccount'))
    expect(target).toHaveAccessibleDescription(t('rules.transferToAccountHint'))
  })

  it('saves the chosen account as the action value', async () => {
    const { user, onSave } = renderDialog()
    const dialog = await screen.findByRole('dialog')

    // Name, then the condition's value (a blank condition can't be saved).
    const [name, conditionValue] = within(dialog).getAllByRole('textbox')
    await user.type(name, 'MercadoPago Buffer')
    await user.type(conditionValue, 'buffer')
    await chooseTransferAction(user, dialog)
    await user.selectOptions(within(dialog).getByLabelText(t('rules.transferToAccount')), 'cofrinho')
    await user.click(within(dialog).getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(onSave).toHaveBeenCalled())
    const saved = onSave.mock.calls[0][0]
    expect(saved.actions).toContainEqual({ op: 'transfer_to_account', value: 'cofrinho' })
  })
})
