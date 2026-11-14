import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { cdiYield } from '@/fork/api'
import { extractApiError } from '@/lib/api-errors'
import { formatCurrency } from '@/lib/format'
import { invalidateFinancialQueries } from '@/lib/invalidate-queries'
import type { CdiYieldMode } from '@/fork/types'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DatePickerInput } from '@/components/ui/date-picker-input'

const SELECT_CLASS =
  'h-9 w-full rounded-lg border border-border bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary'

function formatDateStr(dateStr: string, locale: string): string {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString(locale)
}

function todayIso(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

interface Props {
  accountId: string
  currency: string
  locale: string
  dateLocale: string
  mask: (value: string) => string
  canWrite: boolean
}

/**
 * CDI yield for a manual savings account: the account earns a percentage of
 * CDI and a "Rendimento CDI" credit is posted for each day it accrues.
 */
export function CdiYieldCard({ accountId, currency, locale, dateLocale, mask, canWrite }: Props) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [percent, setPercent] = useState('100')
  const [mode, setMode] = useState<CdiYieldMode>('business_days')
  const [startDate, setStartDate] = useState(todayIso())
  const [confirmOff, setConfirmOff] = useState(false)

  const { data } = useQuery({
    queryKey: ['cdi-yield', accountId],
    queryFn: () => cdiYield.get(accountId),
  })

  const afterChange = (posted?: number) => {
    queryClient.invalidateQueries({ queryKey: ['cdi-yield', accountId] })
    invalidateFinancialQueries(queryClient)
    if (posted !== undefined) toast.success(t('cdiYield.saved', { count: posted }))
  }
  const onError = (err: unknown) => toast.error(extractApiError(err, t('common.error')))

  const save = useMutation({
    mutationFn: () => cdiYield.save(accountId, { percent: Number(percent.replace(',', '.')), mode, start_date: startDate }),
    onSuccess: (res) => {
      setEditing(false)
      afterChange(res.posted_days)
    },
    onError,
  })
  const recalc = useMutation({
    mutationFn: () => cdiYield.recalculate(accountId),
    onSuccess: (res) => afterChange(res.posted_days),
    onError,
  })
  const off = useMutation({
    mutationFn: (deletePosted: boolean) => cdiYield.remove(accountId, deletePosted),
    onSuccess: () => {
      setConfirmOff(false)
      afterChange()
      toast.success(t('cdiYield.turnedOff'))
    },
    onError,
  })

  const startEditing = () => {
    setPercent(String(data?.percent ?? 100))
    setMode(data?.mode ?? 'business_days')
    setStartDate(data?.start_date ?? todayIso())
    setEditing(true)
  }

  const percentNumber = Number(percent.replace(',', '.'))
  const percentValid = Number.isFinite(percentNumber) && percentNumber > 0 && percentNumber <= 300
  const busy = save.isPending || recalc.isPending || off.isPending

  return (
    <div className="bg-card rounded-xl border border-border shadow-sm p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h2 className="text-sm font-semibold">{t('cdiYield.title')}</h2>
        {canWrite && !editing && (
          <div className="flex items-center gap-2">
            {data?.configured && (
              <Button size="sm" variant="outline" className="h-8" disabled={busy} onClick={() => recalc.mutate()}>
                {t('cdiYield.recalculate')}
              </Button>
            )}
            <Button size="sm" variant={data?.configured ? 'outline' : 'default'} className="h-8" onClick={startEditing}>
              {data?.configured ? t('common.edit') : t('cdiYield.setUp')}
            </Button>
          </div>
        )}
      </div>

      {!editing && !data?.configured && (
        <p className="text-sm text-muted-foreground">{t('cdiYield.notConfigured')}</p>
      )}

      {!editing && data?.configured && (
        <>
          <dl className="grid grid-cols-2 lg:grid-cols-4 gap-4 text-sm">
            <div>
              <dt className="text-xs font-medium text-muted-foreground mb-1">{t('cdiYield.rate')}</dt>
              <dd className="font-semibold tabular-nums">{t('cdiYield.percentOfCdi', { percent: data.percent })}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground mb-1">{t('cdiYield.accrual')}</dt>
              <dd className="font-semibold">
                {data.mode === 'calendar_days' ? t('cdiYield.calendarDays') : t('cdiYield.businessDays')}
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground mb-1">{t('cdiYield.since')}</dt>
              <dd className="font-semibold tabular-nums">{data.start_date ? formatDateStr(data.start_date, dateLocale) : '—'}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-muted-foreground mb-1">{t('cdiYield.totalPosted')}</dt>
              <dd className="font-semibold tabular-nums text-emerald-600">
                {mask(formatCurrency(data.total_posted, currency, locale))}
              </dd>
              <dd className="mt-1 text-xs text-muted-foreground">
                {data.last_posted_date
                  ? t('cdiYield.lastPosted', { date: formatDateStr(data.last_posted_date, dateLocale) })
                  : t('cdiYield.nothingYet')}
              </dd>
            </div>
          </dl>
          <p className="text-xs text-muted-foreground">{t('cdiYield.howItWorks')}</p>
          {canWrite && (
            confirmOff ? (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-muted-foreground">{t('cdiYield.turnOffQuestion')}</span>
                <Button size="sm" variant="outline" className="h-8" disabled={busy} onClick={() => off.mutate(false)}>
                  {t('cdiYield.keepPosted')}
                </Button>
                <Button size="sm" variant="outline" className="h-8 text-rose-600" disabled={busy} onClick={() => off.mutate(true)}>
                  {t('cdiYield.deletePosted')}
                </Button>
                <Button size="sm" variant="ghost" className="h-8" onClick={() => setConfirmOff(false)}>
                  {t('common.cancel')}
                </Button>
              </div>
            ) : (
              <button
                type="button"
                className="text-xs text-muted-foreground underline-offset-2 hover:text-rose-600 hover:underline"
                onClick={() => setConfirmOff(true)}
              >
                {t('cdiYield.turnOff')}
              </button>
            )
          )}
        </>
      )}

      {editing && (
        <form
          className="space-y-3"
          onSubmit={(e) => {
            e.preventDefault()
            if (percentValid) save.mutate()
          }}
        >
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="cdi-percent">{t('cdiYield.percentLabel')}</Label>
              <Input
                id="cdi-percent"
                inputMode="decimal"
                value={percent}
                onChange={(e) => setPercent(e.target.value)}
                aria-invalid={!percentValid || undefined}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="cdi-mode">{t('cdiYield.accrual')}</Label>
              <select
                id="cdi-mode"
                className={SELECT_CLASS}
                value={mode}
                onChange={(e) => setMode(e.target.value as CdiYieldMode)}
              >
                <option value="business_days">{t('cdiYield.businessDays')}</option>
                <option value="calendar_days">{t('cdiYield.calendarDays')}</option>
              </select>
            </div>
            <div className="space-y-1">
              <Label>{t('cdiYield.startDate')}</Label>
              <DatePickerInput value={startDate} onChange={setStartDate} />
            </div>
          </div>
          <p className="text-xs text-muted-foreground">
            {mode === 'calendar_days' ? t('cdiYield.calendarDaysHint') : t('cdiYield.businessDaysHint')}
          </p>
          <p className="text-xs text-muted-foreground">{t('cdiYield.rebuildHint')}</p>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={() => setEditing(false)}>
              {t('common.cancel')}
            </Button>
            <Button type="submit" disabled={!percentValid || !startDate || busy}>
              {t('common.save')}
            </Button>
          </div>
        </form>
      )}
    </div>
  )
}
