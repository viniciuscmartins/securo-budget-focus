import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Repeat } from 'lucide-react'

import { recurringLinks } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useRecurringAmountModes } from '@/hooks/use-recurring-amount-modes'
import { suggestedMatch } from '@/lib/recurring-link-utils'
import type { RecurringFromTransactionInput, RecurringTransaction, Transaction } from '@/types'

const SELECT_CLASS =
  'h-9 w-full rounded-md border border-border bg-card px-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

const FREQUENCIES: RecurringTransaction['frequency'][] = [
  'monthly', 'weekly', 'biweekly', 'quarterly', 'semiannual', 'yearly',
]
const MONTHLY_LIKE = new Set(['monthly', 'quarterly', 'semiannual', 'yearly'])

/**
 * "Make recurring" for an existing transaction, synced ones included.
 * Pre-filled from the transaction; can also create a rule so later payments
 * link themselves, and let the expected amount follow the last payments.
 */
export function MakeRecurringPanel({
  transaction,
  onCreated,
}: {
  transaction: Transaction
  onCreated?: (recurring: RecurringTransaction) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const suggestion = suggestedMatch(transaction)
  const [open, setOpen] = useState(false)
  const [description, setDescription] = useState(
    (transaction.payee ?? '').trim() || transaction.description,
  )
  const [frequency, setFrequency] = useState<RecurringTransaction['frequency']>('monthly')
  const [day, setDay] = useState(String(Number(transaction.date.slice(8, 10))))
  const [followAverage, setFollowAverage] = useState(true)
  const [withRule, setWithRule] = useState(true)
  const [ruleField, setRuleField] = useState<'payee' | 'description' | 'notes'>(suggestion.field)
  const [ruleValue, setRuleValue] = useState(suggestion.value)

  const dayNumber = Number(day)
  const dayValid = !MONTHLY_LIKE.has(frequency) || (Number.isInteger(dayNumber) && dayNumber >= 1 && dayNumber <= 31)
  const valid = description.trim() !== '' && dayValid && (!withRule || ruleValue.trim() !== '')

  const mutation = useMutation({
    mutationFn: () => {
      const body: RecurringFromTransactionInput = {
        frequency,
        description: description.trim(),
        follow_average: followAverage,
        link_rule: withRule
          ? {
              field: ruleField,
              op: 'contains',
              value: ruleValue.trim(),
              // Named here so the rule reads in the user's language.
              name: t('recurringLink.ruleName', { name: description.trim() }).slice(0, 255),
            }
          : null,
      }
      if (MONTHLY_LIKE.has(frequency)) body.day_of_month = dayNumber
      return recurringLinks.fromTransaction(transaction.id, body)
    },
    onSuccess: ({ recurring, linked_history }) => {
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
      queryClient.invalidateQueries({ queryKey: ['rules'] })
      toast.success(
        linked_history > 0
          ? t('recurringLink.createdWithHistory', { count: linked_history })
          : t('recurringLink.created'),
      )
      setOpen(false)
      onCreated?.(recurring)
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })

  if (!open) {
    return (
      <div className="flex items-center justify-between gap-2 p-3 text-sm bg-muted/50 border border-border rounded-md">
        <span className="text-muted-foreground">{t('recurringLink.offer')}</span>
        <Button type="button" variant="outline" size="sm" className="gap-1.5 shrink-0" onClick={() => setOpen(true)}>
          <Repeat size={14} />
          {t('recurringLink.makeRecurring')}
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-3 p-3 border border-border rounded-md" role="group" aria-label={t('recurringLink.makeRecurring')}>
      <p className="text-sm font-medium">{t('recurringLink.makeRecurring')}</p>
      <div className="space-y-1.5">
        <Label htmlFor="mr-description">{t('recurring.description')}</Label>
        <Input id="mr-description" value={description} maxLength={500} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="mr-frequency">{t('recurring.frequency')}</Label>
          <select
            id="mr-frequency"
            className={SELECT_CLASS}
            value={frequency}
            onChange={(e) => setFrequency(e.target.value as RecurringTransaction['frequency'])}
          >
            {FREQUENCIES.map((f) => (
              <option key={f} value={f}>{t(`recurring.${f}`)}</option>
            ))}
          </select>
        </div>
        {MONTHLY_LIKE.has(frequency) && (
          <div className="space-y-1.5">
            <Label htmlFor="mr-day">{t('recurringLink.dayOfMonth')}</Label>
            <Input
              id="mr-day"
              type="number"
              min={1}
              max={31}
              value={day}
              onChange={(e) => setDay(e.target.value)}
              aria-invalid={!dayValid || undefined}
            />
          </div>
        )}
      </div>

      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={followAverage}
          onChange={(e) => setFollowAverage(e.target.checked)}
          className="h-4 w-4 mt-0.5 rounded border-border"
        />
        <span className="text-sm">
          {t('recurringLink.followAverage')}
          <span className="block text-xs text-muted-foreground">{t('recurringLink.followAverageHelp')}</span>
        </span>
      </label>

      <label className="flex items-start gap-2 cursor-pointer">
        <input
          type="checkbox"
          checked={withRule}
          onChange={(e) => setWithRule(e.target.checked)}
          className="h-4 w-4 mt-0.5 rounded border-border"
        />
        <span className="text-sm">
          {t('recurringLink.createRule')}
          <span className="block text-xs text-muted-foreground">{t('recurringLink.createRuleHelp')}</span>
        </span>
      </label>
      {withRule && (
        <div className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)] gap-2 pl-6">
          <select
            className={SELECT_CLASS}
            value={ruleField}
            onChange={(e) => setRuleField(e.target.value as 'payee' | 'description' | 'notes')}
            aria-label={t('recurringLink.ruleField')}
          >
            <option value="payee">{t('recurringLink.fieldPayee')}</option>
            <option value="description">{t('recurringLink.fieldDescription')}</option>
            <option value="notes">{t('recurringLink.fieldNotes')}</option>
          </select>
          <Input
            value={ruleValue}
            maxLength={500}
            onChange={(e) => setRuleValue(e.target.value)}
            aria-label={t('recurringLink.ruleValue')}
            placeholder={t('recurringLink.ruleValuePlaceholder')}
          />
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => setOpen(false)}>
          {t('common.cancel')}
        </Button>
        <Button type="button" size="sm" disabled={!valid || mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? t('common.loading') : t('recurringLink.create')}
        </Button>
      </div>
    </div>
  )
}

/** Toggle in the recurring editor: amount follows the average of the last 3 payments. */
export function RecurringAverageToggle({
  recurringId,
  onAmountChange,
}: {
  recurringId: string
  /** The amount the server set, so an open form doesn't save a stale one over it. */
  onAmountChange?: (amount: number) => void
}) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const following = useRecurringAmountModes().has(recurringId)
  const mutation = useMutation({
    mutationFn: (next: boolean) => recurringLinks.setAmountMode(recurringId, next),
    onSuccess: (recurring) => {
      onAmountChange?.(recurring.amount)
      invalidateFinancialQueries(queryClient)
      queryClient.invalidateQueries({ queryKey: ['recurring'] })
    },
    onError: (err: unknown) => toast.error(extractApiError(err, t('common.error'))),
  })
  return (
    <label className="flex items-start gap-2 cursor-pointer">
      <input
        type="checkbox"
        checked={following}
        disabled={mutation.isPending}
        onChange={(e) => mutation.mutate(e.target.checked)}
        className="h-4 w-4 mt-0.5 rounded border-border"
      />
      <span className="text-sm text-foreground">
        {t('recurringLink.followAverage')}
        <span className="block text-xs text-muted-foreground">{t('recurringLink.followAverageToggleHelp')}</span>
      </span>
    </label>
  )
}
