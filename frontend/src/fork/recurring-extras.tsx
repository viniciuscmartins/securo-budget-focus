// The fork's components inside upstream's Recurring page and transaction
// dialog: the "follows the average" badge and "Make recurring" from a
// transaction. The recurring form's additions are in use-recurring-form.tsx.
// Kept here so upstream's files only carry one-line hooks.
import { useTranslation } from 'react-i18next'
import { MakeRecurringPanel } from '@/components/recurring-link'
import { canMakeRecurring } from '@/lib/recurring-link-utils'
import { useRecurringAmountModes } from '@/hooks/use-recurring-amount-modes'
import type { Transaction } from '@/types'

/** "Average" under a recurring item's amount in the list, when it follows its payments. */
export function ForkAverageBadge({ recurringId }: { recurringId: string }) {
  const { t } = useTranslation()
  if (!useRecurringAmountModes().has(recurringId)) return null
  return <div className="text-[11px] font-normal text-muted-foreground">{t('recurringLink.averageBadge')}</div>
}

/** "Make recurring" in the transaction dialog, for a saved transaction not linked yet. */
export function ForkMakeRecurring({
  transaction,
  linked,
  onCreated,
}: {
  transaction: Transaction | null | undefined
  linked: boolean
  onCreated: () => void
}) {
  if (linked || !transaction || !canMakeRecurring(transaction)) return null
  return <MakeRecurringPanel transaction={transaction} onCreated={onCreated} />
}
