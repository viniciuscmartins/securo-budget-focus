import { describe, expect, it } from 'vitest'
import { screen, within } from '@testing-library/react'

import { SidebarAccountGroups } from '@/components/sidebar-account-groups'
import { renderWithProviders } from '@/test/utils'
import type { Account } from '@/types'

function account(partial: Partial<Account>): Account {
  return {
    id: partial.name ?? 'x', name: 'x', type: 'checking', currency: 'BRL',
    balance: 0, current_balance: 0, previous_balance: null, balance_primary: null,
    credit_limit: null, available_credit: null, statement_close_day: null, payment_due_day: null,
    next_close_date: null, next_due_date: null, minimum_payment: null, card_brand: null,
    card_level: null, shared_balance_group: null, is_closed: false, closed_at: null,
    ...partial,
  } as Account
}

const accounts = [
  account({ name: 'Visa', type: 'credit_card', current_balance: -500, next_due_date: '2026-12-20' }),
  account({ name: 'Checking', type: 'checking', current_balance: 180 }),
  account({ name: 'Master', type: 'credit_card', current_balance: -1200, next_due_date: '2026-12-05' }),
  account({ name: 'Savings', type: 'savings', current_balance: 8000 }),
  account({ name: 'Amex', type: 'credit_card', current_balance: -10 }),
]

describe('SidebarAccountGroups', () => {
  it('puts accounts first and cards below, each with its subtotal', () => {
    renderWithProviders(
      <SidebarAccountGroups accounts={accounts} currency="BRL" locale="en-US" mask={(v) => v} onNavigate={() => {}} />,
    )
    const [cash, cards] = screen.getAllByRole('region')
    expect(cash).toHaveAccessibleName('Account balances')
    expect(cash).toHaveTextContent('R$8,180.00')
    expect(within(cash).queryByText('Visa')).toBeNull()
    expect(cards).toHaveAccessibleName('Credit cards')
    expect(cards).toHaveTextContent('-R$1,710.00')
  })

  it('lists cards by the next bill, with its due date', () => {
    renderWithProviders(
      <SidebarAccountGroups accounts={accounts} currency="BRL" locale="en-US" mask={(v) => v} onNavigate={() => {}} />,
    )
    const cards = screen.getByRole('region', { name: 'Credit cards' })
    const names = within(cards).getAllByRole('link').map((l) => l.textContent ?? '')
    expect(names[0]).toContain('Master')
    expect(names[0]).toContain('Bill due Dec 05')
    expect(names[1]).toContain('Visa')
    expect(names[2]).toContain('Amex')
    expect(names[2]).toContain('No due date set')
  })
})
