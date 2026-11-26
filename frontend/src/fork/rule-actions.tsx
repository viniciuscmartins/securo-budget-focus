// The fork's two rule actions, "Mark as transfer with account"
// (`transfer_to_account`) and "Link to recurring" (`link_recurring`), as seen
// by the rule dialog and the Rules page. Kept here so upstream's
// rule-dialog.tsx and rules.tsx only carry one-line hooks.
import { useMemo, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { useQuery } from '@tanstack/react-query'
import { recurring as recurringApi } from '@/lib/api'
import { getAccountName } from '@/lib/account-utils'
import type { RuleAction } from '@/types'

type AccountLike = { id: string; name: string; display_name?: string | null }
type ForkAccount = AccountLike & { connection_id?: string | null; is_closed?: boolean }
type RecurringLike = { id: string; description: string }

const FORK_OPS = new Set(['transfer_to_account', 'link_recurring'])

/** Entries for the Rules page's action filter. */
export const FORK_ACTION_FILTERS = [
  { value: 'transfer_to_account', label: 'rules.transferToAccount' },
  { value: 'link_recurring', label: 'recurringLink.linkToRecurring' },
] as const

/** Recurring items, under the same query key as upstream's Recurring and Rules pages. */
function useRecurringItems(enabled = true): RecurringLike[] {
  const { data } = useQuery({
    queryKey: ['recurring'],
    queryFn: () => recurringApi.list(),
    enabled,
  })
  return data ?? []
}

/**
 * The fork's actions inside the rule dialog: their options in the action
 * picker, their value fields and the transfer hint.
 */
export function useForkRuleActions(open: boolean, accounts: AccountLike[]) {
  const { t } = useTranslation()
  // Wherever the dialog is opened from (Rules page, a transaction, the
  // dashboard), it loads the recurring items itself; the cache is shared.
  const recurringItems = useRecurringItems(open)
  // "Mark as transfer with account" only targets manual, open accounts: a synced one gets
  // its own copy of the movement from the bank, so a counterpart would count
  // the money twice. The server enforces the same rule.
  const transferTargets = useMemo(
    () => (accounts as ForkAccount[]).filter((acc) => !acc.connection_id && !acc.is_closed),
    [accounts],
  )

  const options: ReactNode = (
    <>
      <option value="transfer_to_account">{t('rules.transferToAccount')}</option>
      <option value="link_recurring">{t('recurringLink.linkToRecurring')}</option>
    </>
  )

  function handles(op: string): boolean {
    return FORK_OPS.has(op)
  }

  function valueField(
    action: RuleAction,
    i: number,
    update: (i: number, field: keyof RuleAction, value: string) => void,
    selectClass: string,
  ): ReactNode {
    const className = `${selectClass} w-full min-w-0 sm:w-0 sm:flex-1`
    if (action.op === 'transfer_to_account') {
      return (
        <select
          className={className}
          value={action.value}
          onChange={(e) => update(i, 'value', e.target.value)}
          aria-label={t('rules.transferToAccount')}
          aria-describedby={`action-${i}-transfer-hint`}
          required
        >
          <option value="">
            {transferTargets.length ? t('rules.selectManualAccount') : t('rules.noManualAccounts')}
          </option>
          {transferTargets.map((acc) => (
            <option key={acc.id} value={acc.id}>{getAccountName(acc)}</option>
          ))}
        </select>
      )
    }
    return (
      <select
        className={className}
        value={action.value}
        onChange={(e) => update(i, 'value', e.target.value)}
        aria-label={t('recurringLink.linkToRecurring')}
        required
      >
        <option value="">
          {recurringItems.length ? t('recurringLink.selectRecurring') : t('recurringLink.noRecurring')}
        </option>
        {recurringItems.map((item) => (
          <option key={item.id} value={item.id}>{item.description}</option>
        ))}
      </select>
    )
  }

  function hint(action: RuleAction, i: number): ReactNode {
    if (action.op !== 'transfer_to_account') return null
    // Which way the money goes depends on the matched row, not on the rule,
    // so say it rather than imply "to".
    return (
      <p id={`action-${i}-transfer-hint`} className="text-xs text-muted-foreground">
        {t('rules.transferToAccountHint')}
      </p>
    )
  }

  return { options, handles, valueField, hint }
}

/**
 * Summary text for the fork's actions on the Rules page; `undefined` for any
 * other action, which upstream's summary handles.
 */
export function useForkActionSummary(accounts: AccountLike[]) {
  const recurringItems = useRecurringItems()
  return (a: RuleAction, arrow: string, t: (key: string) => string): string | undefined => {
    if (a.op === 'transfer_to_account') {
      const acc = accounts.find((x) => x.id === a.value)
      return `${arrow} ${t('rules.transferToAccount')}${acc ? `: ${getAccountName(acc)}` : ''}`
    }
    if (a.op === 'link_recurring') {
      const item = recurringItems.find((r) => r.id === a.value)
      return `${arrow} ${t('recurringLink.linkToRecurring')}${item ? `: ${item.description}` : ''}`
    }
    return undefined
  }
}
