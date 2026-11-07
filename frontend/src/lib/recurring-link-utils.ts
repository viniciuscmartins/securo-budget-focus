import type { Transaction } from '@/types'

/** The text a rule should look for: the payee when there is one, else the description. */
export function suggestedMatch(tx: Pick<Transaction, 'payee' | 'description'>): {
  field: 'payee' | 'description'
  value: string
} {
  const payee = (tx.payee ?? '').trim()
  if (payee) return { field: 'payee', value: payee }
  return { field: 'description', value: (tx.description ?? '').trim() }
}

/** Whether a transaction can become the first payment of a recurring item. */
export function canMakeRecurring(
  tx: Pick<Transaction, 'source' | 'recurring_transaction_id' | 'transfer_pair_id'> | null,
): boolean {
  if (!tx) return false
  return (
    tx.recurring_transaction_id == null
    && tx.transfer_pair_id == null
    && tx.source !== 'recurring'
    && tx.source !== 'opening_balance'
  )
}
