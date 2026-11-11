import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'

import { getAccountName, sidebarAccountBalance, sortAccountsByAbsoluteBalance } from '@/lib/account-utils'
import { formatCurrency } from '@/lib/format'
import type { Account } from '@/types'

interface Props {
  accounts: Account[]
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
 * then credit cards with the date their bill is due. Each group shows its
 * own subtotal; the overall total stays in the header above.
 */
export function SidebarAccountGroups({ accounts, currency, locale, mask, onNavigate }: Props) {
  const { t } = useTranslation()
  const amount = (a: Account) => sidebarAccountBalance(a).amount
  const cards = accounts.filter((a) => a.type === 'credit_card')
  const cash = accounts.filter((a) => a.type !== 'credit_card')
  // Cards by the next bill: the one due first comes first.
  const byDue = [...cards].sort((a, b) => (a.next_due_date ?? '9999').localeCompare(b.next_due_date ?? '9999'))
  const groups = [
    { key: 'cash', title: t('accounts.sidebarCashGroup'), rows: sortAccountsByAbsoluteBalance(cash, amount) },
    { key: 'cards', title: t('accounts.sidebarCardsGroup'), rows: byDue },
  ].filter((g) => g.rows.length > 0)

  return (
    <div className="mt-1 space-y-2">
      {groups.map((group) => {
        const subtotal = group.rows.reduce((sum, a) => sum + amount(a), 0)
        return (
          <section key={group.key} aria-label={group.title}>
            <div className="flex items-center justify-between px-3 pb-0.5 pt-1 text-[10px] font-semibold uppercase tracking-wide text-sidebar-muted/70">
              <span>{group.title}</span>
              <span className={`tabular-nums ${subtotal < 0 ? 'text-rose-400' : ''}`}>
                {mask(formatCurrency(subtotal, currency, locale))}
              </span>
            </div>
            <div className="space-y-0.5">
              {group.rows.map((acc) => {
                const { amount: balance, isPrimary } = sidebarAccountBalance(acc)
                const typeKey = acc.type
                  .replace(/_([a-z])/g, (_, c: string) => c.toUpperCase())
                  .replace(/^./, (c) => c.toUpperCase())
                const due = acc.type === 'credit_card' ? shortDate(acc.next_due_date, locale) : null
                const detail = acc.type === 'credit_card'
                  ? due
                    ? t('accounts.sidebarBillDue', { date: due })
                    : t('accounts.sidebarNoDueDate')
                  : t(`accounts.type${typeKey}`)
                return (
                  <Link
                    key={acc.id}
                    to={`/accounts/${acc.id}`}
                    onClick={onNavigate}
                    className="flex items-center justify-between rounded-lg px-3 py-1.5 text-xs text-sidebar-muted transition-all hover:bg-sidebar-accent hover:text-sidebar-foreground"
                  >
                    <div className="min-w-0 truncate">
                      <span className="block truncate font-medium">{getAccountName(acc)}</span>
                      <span className="block truncate text-[10px] text-sidebar-muted/60">{detail}</span>
                    </div>
                    <span
                      className={`ml-2 shrink-0 text-right text-xs font-medium tabular-nums ${balance < 0 ? 'text-rose-400' : 'text-sidebar-foreground'}`}
                    >
                      {mask(formatCurrency(balance, isPrimary ? currency : acc.currency, locale))}
                    </span>
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
