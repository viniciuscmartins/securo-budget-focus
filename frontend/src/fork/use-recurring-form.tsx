// The fork's additions to upstream's recurring item form: the amount lock
// while it follows the average, the editable next date and the "follow the
// average" toggle. Kept here so upstream's recurring.tsx only carries
// one-line hooks.
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Label } from '@/components/ui/label'
import { DatePickerInput } from '@/components/ui/date-picker-input'
import { RecurringAverageToggle } from '@/components/recurring-link'
import { useRecurringAmountModes } from '@/hooks/use-recurring-amount-modes'
import type { RecurringTransaction } from '@/types'

/**
 * The fork's parts of the recurring item form. `recurring` is the item being
 * edited (null when creating one); `setAmount` is the form's amount setter.
 */
export function useForkRecurringForm(
  recurring: RecurringTransaction | null | undefined,
  setAmount: (value: string) => void,
) {
  const { t } = useTranslation()
  // While the amount follows the average of the last payments the server
  // keeps rewriting it, so a typed value would be lost: turn the option off
  // to type one.
  const followsAverage = useRecurringAmountModes(!!recurring).has(recurring?.id ?? '')
  // The next date can be set back one period, to undo a payment linked by
  // mistake. Sent only when changed.
  const [nextDate, setNextDate] = useState(recurring?.next_occurrence ?? '')

  const payload: { next_occurrence?: string } =
    recurring && nextDate && nextDate !== recurring.next_occurrence ? { next_occurrence: nextDate } : {}

  // Spread after upstream's own props on the amount input: they only change
  // anything while the amount follows the average.
  const amountInputProps = followsAverage
    ? { readOnly: true, 'aria-describedby': 'recurring-amount-average', className: 'bg-muted text-muted-foreground' }
    : {}

  // Full width under the amount row: a third-width column wraps it to five lines.
  const amountHint: ReactNode = followsAverage ? (
    <p id="recurring-amount-average" className="-mt-2 text-xs text-muted-foreground">{t('recurringLink.amountFollowsAverage')}</p>
  ) : null

  const nextDateField: ReactNode = recurring ? (
    <div className="space-y-2">
      <Label>{t('recurring.nextDate')}</Label>
      <DatePickerInput value={nextDate} onChange={setNextDate} placeholder={t('recurring.nextDate')} className="w-full justify-start" />
      <p className="text-xs text-muted-foreground">{t('recurring.nextDateHelp')}</p>
    </div>
  ) : null

  const averageToggle: ReactNode = recurring ? (
    <RecurringAverageToggle recurringId={recurring.id} onAmountChange={(value) => setAmount(String(value))} />
  ) : null

  return { payload, amountInputProps, amountHint, nextDateField, averageToggle }
}
