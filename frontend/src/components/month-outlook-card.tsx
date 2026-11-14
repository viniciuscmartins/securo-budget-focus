import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, CheckCircle2, ChevronDown } from 'lucide-react'

import { monthOutlook } from '@/fork/api'
import { formatCurrency } from '@/lib/format'
import { cn } from '@/lib/utils'
import { Skeleton } from '@/components/ui/skeleton'
import type { MonthOutlook } from '@/fork/types'

function shortDate(iso: string, locale: string) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(locale, { day: '2-digit', month: 'short' })
}

/**
 * "Am I good this month?": money in the accounts now, plus income still
 * expected, minus bills and card bills still due this month. Card
 * installments on later bills are shown apart and never subtracted.
 */
export function MonthOutlookCard({
  month,
  accountIds,
  locale,
  dateLocale,
  mask,
}: {
  /** YYYY-MM */
  month: string
  /** The active Collection's accounts; undefined for all accounts. */
  accountIds?: string[]
  locale: string
  dateLocale: string
  mask: (value: string) => string
}) {
  const { t } = useTranslation()
  const [showDetails, setShowDetails] = useState(false)
  const { data, isLoading, isError } = useQuery({
    // Under 'dashboard' so every financial mutation refreshes it
    // (invalidateFinancialQueries), like the rest of the dashboard.
    queryKey: ['dashboard', 'month-outlook', month, accountIds ?? null],
    queryFn: () => monthOutlook.get(`${month}-01`, accountIds),
  })

  if (isError) return null

  const money = (value: number, currency: string) => mask(formatCurrency(value, currency, locale))

  return (
    <section
      aria-label={t('monthOutlook.title')}
      className="bg-card rounded-xl border border-border shadow-sm mb-5 px-5 pt-4 pb-4"
    >
      <h2 className="text-xs font-semibold text-muted-foreground mb-3">{t('monthOutlook.title')}</h2>
      {isLoading || !data ? (
        <Skeleton className="h-24 w-full" />
      ) : (
        <Outlook
          data={data}
          money={money}
          dateLocale={dateLocale}
          showDetails={showDetails}
          onToggle={() => setShowDetails((v) => !v)}
        />
      )}
    </section>
  )
}

function Outlook({
  data,
  money,
  dateLocale,
  showDetails,
  onToggle,
}: {
  data: MonthOutlook
  money: (value: number, currency: string) => string
  dateLocale: string
  showDetails: boolean
  onToggle: () => void
}) {
  const { t } = useTranslation()
  const c = data.currency
  const isThisMonth = data.month.slice(0, 7) === data.today.slice(0, 7)
  const good = data.end_balance >= 0 && data.lowest_balance >= 0
  const dipsBelowZero = data.lowest_balance < 0

  const rows: { label: string; value: number; sign: '' | '+' | '−' }[] = [
    { label: isThisMonth ? t('monthOutlook.cashNow') : t('monthOutlook.startBalance'), value: data.start_balance, sign: '' },
    { label: t('monthOutlook.incomeExpected'), value: data.income_expected, sign: '+' },
    { label: t('monthOutlook.billsExpected'), value: data.bills_expected, sign: '−' },
    { label: t('monthOutlook.cardBills'), value: data.card_bills, sign: '−' },
  ]

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-xs text-muted-foreground">{t('monthOutlook.endOfMonth')}</p>
          <p className={cn('text-2xl font-bold tabular-nums', data.end_balance < 0 ? 'text-rose-500' : 'text-foreground')}>
            {money(data.end_balance, c)}
          </p>
        </div>
        <p
          className={cn(
            'flex items-center gap-1.5 text-sm font-medium',
            good ? 'text-emerald-600' : 'text-amber-700 dark:text-amber-400',
          )}
        >
          {good ? <CheckCircle2 size={16} /> : <AlertTriangle size={16} />}
          {good
            ? t('monthOutlook.covered')
            : dipsBelowZero && data.end_balance >= 0
              ? t('monthOutlook.dipsBelowZero', { date: shortDate(data.lowest_date, dateLocale) })
              : t('monthOutlook.notCovered')}
        </p>
      </div>

      <dl className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2 sm:gap-x-8">
        {rows.map((row) => (
          <div key={row.label} className="flex justify-between gap-3">
            <dt className="text-muted-foreground">{row.label}</dt>
            <dd className="tabular-nums">{row.sign}{row.sign && ' '}{money(row.value, c)}</dd>
          </div>
        ))}
        <div className="flex justify-between gap-3">
          <dt className="text-muted-foreground">{t('monthOutlook.lowestPoint')}</dt>
          <dd className={cn('tabular-nums', dipsBelowZero && 'text-rose-500')}>
            {money(data.lowest_balance, c)} · {shortDate(data.lowest_date, dateLocale)}
          </dd>
        </div>
        {data.committed_later > 0 && (
          <div className="flex justify-between gap-3">
            <dt className="text-muted-foreground">{t('monthOutlook.committedLater')}</dt>
            <dd className="tabular-nums text-muted-foreground">{money(data.committed_later, c)}</dd>
          </div>
        )}
      </dl>
      {data.committed_later > 0 && (
        <p className="text-xs text-muted-foreground">{t('monthOutlook.committedLaterHint')}</p>
      )}

      {(data.events.length > 0 || data.card_bill_details.length > 0) && (
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={showDetails}
          className="flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          <ChevronDown size={14} className={cn('transition-transform', showDetails && 'rotate-180')} />
          {showDetails ? t('monthOutlook.hideDetails') : t('monthOutlook.showDetails')}
        </button>
      )}

      {showDetails && (
        <div className="space-y-3 border-t border-border pt-3">
          {data.card_bill_details.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground mb-1">{t('monthOutlook.cardBillsTitle')}</p>
              <ul className="space-y-1 text-sm">
                {data.card_bill_details.map((bill) => (
                  <li key={`${bill.account_id}-${bill.due_date}`} className="flex justify-between gap-3">
                    <span className="min-w-0 truncate">
                      {bill.account_name}
                      <span className="text-muted-foreground"> · {t('monthOutlook.due', { date: shortDate(bill.due_date, dateLocale) })}</span>
                      {bill.paid > 0 && (
                        <span className="text-muted-foreground"> · {t('monthOutlook.paidSoFar', { amount: money(bill.paid, c) })}</span>
                      )}
                    </span>
                    <span className="tabular-nums shrink-0">{money(bill.amount, c)}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {data.events.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-muted-foreground mb-1">{t('monthOutlook.timeline')}</p>
              <ul className="space-y-1 text-sm">
                {data.events.map((event, i) => (
                  <li key={`${event.date}-${i}`} className="flex justify-between gap-3">
                    <span className="min-w-0 truncate">
                      <span className="text-muted-foreground tabular-nums">{shortDate(event.date, dateLocale)}</span>
                      {' '}
                      {event.description || t(`monthOutlook.kind.${event.kind}`)}
                      {event.kind === 'card_bill' && (
                        <span className="text-muted-foreground"> · {t('monthOutlook.kind.card_bill')}</span>
                      )}
                      {event.overdue && (
                        <span className="text-amber-700 dark:text-amber-400"> · {t('monthOutlook.overdue')}</span>
                      )}
                    </span>
                    <span className={cn('tabular-nums shrink-0', event.amount > 0 ? 'text-emerald-600' : 'text-foreground')}>
                      {event.amount > 0 ? '+' : '−'}{money(Math.abs(event.amount), c)}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
