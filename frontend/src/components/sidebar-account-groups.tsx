import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { getAccountName, sidebarAccountBalance, sortAccountsByAbsoluteBalance } from '@/lib/account-utils'
import { formatCurrency } from '@/lib/format'
import type { Account, MonthOutlookCardBill } from '@/types'

interface Props {
  accounts: Account[]
  /** Each card's next bill still to pay (primary currency); undefined while loading. */
  cardBills?: MonthOutlookCardBill[]
  currency: string
  locale: string
  mask: (value: string) => string
  onNavigate: () => void
}

/** "2026-12-10" as a short local date ("10/12", "Dec 10"), or null. */
function shortDate(iso: string | null | undefined, locale: string): string | null {
  if (!iso) return null
  const [y, m, d] = iso.slice(0, 10).split('-').map(Number)
  if (!y || !m || !d) return null
  return new Intl.DateTimeFormat(locale, { day: '2-digit', month: 'short' }).format(new Date(y, m - 1, d))
}

/**
 * The sidebar's account list in two groups: accounts holding money first,
 * then credit cards. A card leads with its next bill still to pay, with the
 * card's total and the due date under it, and the cards' subtotal adds up
 * those bills. Until the bills load (or for a card with nothing to pay) the
 * card shows its total, as before. The overall total stays in the header.
 */
export function SidebarAccountGroups({ accounts, cardBills, currency, locale, mask, onNavigate }: Props) {
  const { t } = useTranslation()
  const amount = (a: Account) => sidebarAccountBalance(a).amount
  const billFor = new Map((cardBills ?? []).map((b) => [b.account_id, b]))
  const cardAmount = (a: Account) => {
    const bill = billFor.get(a.id)
    return bill ? -bill.amount : cardBills ? 0 : amount(a)
  }
  const dueDate = (a: Account) => billFor.get(a.id)?.due_date ?? a.next_due_date
  const cards = accounts.filter((a) => a.type === 'credit_card')
  const cash = accounts.filter((a) => a.type !== 'credit_card')
  // Cards by the next bill: the one due first comes first.
  const byDue = [...cards].sort((a, b) => (dueDate(a) ?? '9999').localeCompare(dueDate(b) ?? '9999'))
  const groups = [
    { key: 'cash', title: t('accounts.sidebarCashGroup'), rows: sortAccountsByAbsoluteBalance(cash, amount) },
    { key: 'cards', title: t('accounts.sidebarCardsGroup'), rows: byDue },
  ].filter((g) => g.rows.length > 0)

  return (
    <div className="mt-1 space-y-2">
      {groups.map((group) => {
        const isCards = group.key === 'cards'
        const subtotal = group.rows.reduce((sum, a) => sum + (isCards ? cardAmount(a) : amount(a)), 0)
        return (
          <section key={group.key} aria-label={group.title}>
            <div className="flex items-center justify-between px-3 pb-0.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-sidebar-muted/70">
              <span>{group.title}</span>
              <span
                className={`tabular-nums ${subtotal < 0 ? 'text-rose-400' : ''}`}
                title={isCards && cardBills ? t('accounts.sidebarCardsSubtotalHint') : undefined}
              >
                {mask(formatCurrency(subtotal, currency, locale))}
              </span>
            </div>
            <div className="space-y-0.5">
              {group.rows.map((acc) => {
                const { amount: balance, isPrimary } = sidebarAccountBalance(acc)
                const bill = billFor.get(acc.id)
                const totalText = mask(formatCurrency(balance, isPrimary ? currency : acc.currency, locale))
                const typeKey = acc.type
                  .replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
                  .replace(/^./, (c) => c.toUpperCase())
                const due = acc.type === 'credit_card' ? shortDate(dueDate(acc), locale) : null
                let detail: string
                if (acc.type !== 'credit_card') detail = t(`accounts.type${typeKey}`)
                else if (bill?.overdue) detail = t('accounts.sidebarBillOverdue', { date: due })
                else if (bill) detail = t('accounts.sidebarBillDue', { date: due })
                else if (cardBills) detail = t('accounts.sidebarNoOpenBill')
                else detail = due ? t('accounts.sidebarBillDue', { date: due }) : t('accounts.sidebarNoDueDate')
                // The bill still to pay leads; the card's total moves to the line under it.
                const shown = bill ? -bill.amount : balance
                const shownText = bill ? mask(formatCurrency(shown, currency, locale)) : totalText
                return (
                  <Link
                    key={acc.id}
                    to={`/accounts/${acc.id}`}
                    onClick={onNavigate}
                    className="flex items-center justify-between rounded-lg px-3 py-1.5 text-xs text-sidebar-muted transition-all hover:bg-sidebar-accent hover:text-sidebar-foreground"
                  >
                    <div className="min-w-0 truncate">
                      <span className="block truncate font-medium">{getAccountName(acc)}</span>
                      <span className={`block truncate text-[10px] ${bill?.overdue ? 'text-rose-400' : 'text-sidebar-muted/60'}`}>
                        {detail}
                      </span>
                    </div>
                    <div className="ml-2 shrink-0 text-right">
                      <span
                        className={`block text-xs font-medium tabular-nums ${shown < 0 ? 'text-rose-400' : 'text-sidebar-foreground'}`}
                      >
                        {shownText}
                      </span>
                      {bill && (
                        <span className="block text-[10px] tabular-nums text-sidebar-muted/60">
                          {t('accounts.sidebarCardTotal', { total: totalText })}
                        </span>
                      )}
                    </div>
                  </Link>
                )
              })}
            </div>
          </section>
        )
      })}
    </div>
  )
}
