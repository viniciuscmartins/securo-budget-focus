import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { CalendarDays, ChevronDown, Sparkles } from 'lucide-react'
import { budgets as budgetsApi } from '@/lib/api'
import { extractApiError } from '@/lib/api-errors'
import {
  MONTH_NUMBERS,
  cellVariance,
  isFutureMonth,
  parseAmount,
  percentUsed,
} from '@/lib/budget-plan-utils'
import { useDisplayLocale } from '@/hooks/use-display-locale'
import { usePrivacyMode } from '@/hooks/use-privacy-mode'
import { useAuth } from '@/contexts/auth-context'
import { useWorkspace } from '@/contexts/workspace-context'
import type { BudgetYearCell, BudgetYearPlan, BudgetYearRow } from '@/types'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { PageHeader } from '@/components/page-header'
import { CategoryIcon } from '@/components/category-icon'
import { BudgetSuggestionDialog } from '@/components/budget-suggestion-dialog'

type View = 'grid' | 'month'
type Metric = 'both' | 'planned' | 'variance'
type Cell = Pick<BudgetYearCell, 'planned' | 'actual'>

const OVER = 'text-rose-500'
const UNDER = 'text-emerald-600'
const SHORT = 'text-amber-600'
const UNCATEGORIZED_ID = '__uncategorized__'

/** Money with no category, shown as a read-only row (it can't have a budget). */
function uncategorizedRow(
  plan: BudgetYearPlan,
  name: string,
  kind: 'spending' | 'income' = 'spending',
): BudgetYearRow | null {
  const byMonth = kind === 'income' ? plan.uncategorized_income_by_month : plan.uncategorized_by_month
  const total = kind === 'income' ? plan.uncategorized_income_total : plan.uncategorized_total
  if (!(total > 0)) return null
  return {
    category_id: UNCATEGORIZED_ID,
    category_name: name,
    category_icon: 'circle-help',
    category_color: '#6B7280',
    group_id: null,
    group_name: null,
    cells: byMonth.map((actual, i) => ({ month: i + 1, planned: null, actual })),
    planned_total: 0,
    actual_total: total,
  }
}

const NAV_BUTTON =
  'h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-card text-muted-foreground hover:text-foreground transition-all text-base'

const TOOLTIP_STYLE = {
  background: 'var(--card)',
  color: 'var(--foreground)',
  border: '1px solid var(--border)',
  borderRadius: 8,
  fontSize: 12,
}

/** A table's title, hint and the button that hides its category rows. */
function SectionHeader({
  title,
  hint,
  collapsed,
  onToggle,
}: {
  title: string
  hint?: string
  collapsed?: boolean
  onToggle?: () => void
}) {
  const { t } = useTranslation()
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 pt-4 sm:px-5">
      <div className="flex items-center gap-2">
        {onToggle && (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={!collapsed}
            aria-label={collapsed ? t('budgetPlan.showCategories') : t('budgetPlan.hideCategories')}
            title={collapsed ? t('budgetPlan.showCategories') : t('budgetPlan.hideCategories')}
            className="flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <ChevronDown size={14} className={collapsed ? '-rotate-90 transition-transform' : 'transition-transform'} />
          </button>
        )}
        <p className="text-sm font-semibold text-foreground">{title}</p>
      </div>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}

/** The month headers shared by every table. */
function MonthHeaderRow({ monthNames, first }: { monthNames: string[]; first: string }) {
  const { t } = useTranslation()
  return (
    <tr className="border-b border-border">
      <th className="sticky left-0 z-10 bg-card py-3 pl-4 pr-2 text-left font-medium text-muted-foreground sm:pl-5">
        {first}
      </th>
      {MONTH_NUMBERS.map((m) => (
        <th key={m} className="px-1 py-3 text-center font-medium capitalize text-muted-foreground">
          {monthNames[m - 1]}
        </th>
      ))}
      <th className="px-2 py-3 pr-4 text-center font-medium text-muted-foreground sm:pr-5">
        {t('budgetPlan.yearColumn')}
      </th>
    </tr>
  )
}

/** Which tables show only their totals; remembered in this browser. */
type Collapsed = { income: boolean; spending: boolean; moved: boolean }
const COLLAPSED_KEY = 'budgetPlan.collapsed'

function useCollapsed(): [Collapsed, (next: Collapsed) => void] {
  const [collapsed, setCollapsed] = useState<Collapsed>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(COLLAPSED_KEY) ?? 'null')
      if (saved && typeof saved === 'object') {
        return { income: !!saved.income, spending: !!saved.spending, moved: !!saved.moved }
      }
    } catch {
      // No storage (private window): start expanded.
    }
    return { income: false, spending: false, moved: false }
  })
  const update = (next: Collapsed) => {
    setCollapsed(next)
    try {
      localStorage.setItem(COLLAPSED_KEY, JSON.stringify(next))
    } catch {
      // Not remembered, still applied.
    }
  }
  return [collapsed, update]
}

function SectionCard({ children, label }: { children: React.ReactNode; label?: string }) {
  if (label) {
    return (
      <section aria-label={label} className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">
        {children}
      </section>
    )
  }
  return <div className="bg-card rounded-xl border border-border shadow-sm overflow-hidden">{children}</div>
}

function SummaryCard({ label, value, tone, hint }: { label: string; value: string; tone?: string; hint?: string }) {
  return (
    <div className="bg-card rounded-xl border border-border shadow-sm px-4 py-3">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${tone ?? 'text-foreground'}`}>{value}</p>
      {hint && <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">{hint}</p>}
    </div>
  )
}

function Dash() {
  return <span className="text-muted-foreground">—</span>
}

/** A signed planned-minus-actual amount: green when money is left, red when over. */
function Variance({ value, fmt }: { value: number | null; fmt: (n: number) => string }) {
  if (value == null) return <Dash />
  return (
    <span className={value < 0 ? OVER : UNDER}>
      {value > 0 ? '+' : ''}
      {fmt(value)}
    </span>
  )
}

/**
 * The Transactions page filtered to what a plan cell counts: posted
 * transactions of one category in one month (or the whole year when `month`
 * is null). The list filters by the same date the plan uses, so a card
 * purchase shows in the month of its bill, as it does here.
 */
function transactionsHref(year: number, month: number | null, categoryId: string, kind: 'spending' | 'income') {
  const pad = (n: number) => String(n).padStart(2, '0')
  const first = month ?? 1
  const last = month ?? 12
  const lastDay = new Date(year, last, 0).getDate()
  const range = `from=${year}-${pad(first)}-01&to=${year}-${pad(last)}-${pad(lastDay)}&status=posted`
  if (categoryId === UNCATEGORIZED_ID) {
    return `/transactions?${range}&uncategorized=1&type=${kind === 'income' ? 'credit' : 'debit'}`
  }
  return `/transactions?${range}&category_id=${encodeURIComponent(categoryId)}`
}

interface CellLink {
  href: string
  label: string
}

/** An actual amount that opens its transactions; plain text when there is nothing to open. */
function ActualAmount({ link, className, children }: { link?: CellLink; className: string; children: React.ReactNode }) {
  if (!link) return <span className={className}>{children}</span>
  return (
    <Link
      to={link.href}
      aria-label={link.label}
      title={link.label}
      className={`${className} rounded-sm underline-offset-2 hover:underline focus-visible:underline focus-visible:outline-none`}
    >
      {children}
    </Link>
  )
}

/** Actual on top, plan underneath; coloured against the plan once the month has happened. */
function ActualVsPlan({
  cell,
  future,
  fmt,
  noBudget,
  compareTo,
  link,
}: {
  cell: Cell
  future: boolean
  fmt: (n: number) => string
  noBudget: string
  /** Where the actual leads; ignored while there is nothing spent. */
  link?: CellLink
  /**
   * What the actual is judged against, when that differs from the plan shown.
   * The Year column shows the full-year plan but must colour against the plan
   * for months that have already happened, or a category that is over every
   * single month would still look fine until December.
   */
  compareTo?: number | null
}) {
  const hasSpend = !future && cell.actual > 0
  if (cell.planned == null && !hasSpend) return <Dash />
  const target = compareTo !== undefined ? compareTo : cell.planned
  const over = cell.planned != null && target != null && !future && cell.actual > target
  const tone = future ? 'text-muted-foreground' : cell.planned == null ? 'text-foreground' : over ? OVER : UNDER
  return (
    <span className="flex flex-col items-center leading-tight">
      <ActualAmount link={hasSpend ? link : undefined} className={`font-medium ${tone}`}>
        {future ? '—' : fmt(cell.actual)}
      </ActualAmount>
      <span className="text-[10px] text-muted-foreground">{cell.planned == null ? noBudget : fmt(cell.planned)}</span>
    </span>
  )
}

/** Editable planned amount. Saves on blur or Enter; empty clears the month's budget. */
function PlannedInput({
  value,
  label,
  onCommit,
  resetKey,
}: {
  value: number | null
  label: string
  onCommit: (amount: number | null) => void
  /** Bumped after a failed save, so the field drops what was typed. */
  resetKey: number
}) {
  const saved = value == null ? '' : String(value)
  return (
    <input
      // Re-mount when the saved value changes, or a save failed, so the field
      // always shows what the server holds.
      key={`${saved}:${resetKey}`}
      type="number"
      min={0}
      step={1}
      inputMode="decimal"
      aria-label={label}
      defaultValue={saved}
      className="h-7 w-[4.25rem] rounded-md border border-transparent bg-transparent px-1 text-right text-xs tabular-nums text-foreground hover:border-border focus:border-border focus:bg-card focus:outline-none focus:ring-2 focus:ring-primary"
      onKeyDown={(e) => {
        if (e.key === 'Enter') e.currentTarget.blur()
        if (e.key === 'Escape') {
          e.currentTarget.value = saved
          e.currentTarget.blur()
        }
      }}
      onBlur={(e) => {
        const parsed = parseAmount(e.currentTarget.value)
        if (parsed === undefined) {
          e.currentTarget.value = saved // not a valid amount: put back what is saved
          return
        }
        if (parsed !== value) onCommit(parsed)
      }}
    />
  )
}

export default function BudgetPlanPage() {
  const { t, i18n } = useTranslation()
  const { mask, privacyMode } = usePrivacyMode()
  const { user } = useAuth()
  const { canWrite } = useWorkspace()
  const queryClient = useQueryClient()
  const numberLocale = useDisplayLocale()
  const uiLocale = i18n.resolvedLanguage ?? i18n.language
  const currency = user?.preferences?.currency_display ?? 'USD'

  const today = new Date()
  const [year, setYear] = useState(today.getFullYear())
  const [view, setView] = useState<View>('grid')
  const [metric, setMetric] = useState<Metric>('both')
  const [collapsed, setCollapsed] = useCollapsed()
  const onlyTotals = collapsed.income && collapsed.spending && collapsed.moved
  const [month, setMonth] = useState(today.getMonth() + 1)
  const [suggestOpen, setSuggestOpen] = useState(false)
  const [saveFailures, setSaveFailures] = useState(0)

  const money = useMemo(
    () => new Intl.NumberFormat(numberLocale, { style: 'currency', currency, maximumFractionDigits: 0 }),
    [numberLocale, currency],
  )
  const fmt = (n: number) => mask(money.format(Math.round(n) === 0 ? 0 : n))

  const monthNames = useMemo(
    () => MONTH_NUMBERS.map((m) => new Date(2000, m - 1, 1).toLocaleDateString(uiLocale, { month: 'short' })),
    [uiLocale],
  )
  const longMonthName = (m: number) => new Date(2000, m - 1, 1).toLocaleDateString(uiLocale, { month: 'long' })

  const { data: plan, isLoading, isError } = useQuery({
    queryKey: ['budget-year-plan', year],
    queryFn: () => budgetsApi.yearPlan(year),
  })

  const saveCell = useMutation({
    mutationFn: (v: { categoryId: string; month: number; amount: number | null }) =>
      budgetsApi.applyYearPlan(year, [{ category_id: v.categoryId, month: v.month, amount: v.amount }]),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['budget-year-plan'] })
      queryClient.invalidateQueries({ queryKey: ['budgets'] })
    },
    onError: (err: unknown) => {
      setSaveFailures((n) => n + 1)
      toast.error(extractApiError(err, t('common.error')))
    },
  })

  const noBudget = t('budgetPlan.noBudget')

  const header = (
    <PageHeader
      section={t('budgets.title')}
      title={t('budgetPlan.title')}
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to="/budgets/monthly"
            className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-border bg-card px-2.5 text-sm text-foreground transition-all hover:bg-muted/50"
          >
            <CalendarDays size={14} className="text-muted-foreground" /> {t('budgetPlan.monthly')}
          </Link>
          <div className="flex items-center gap-1">
            <button className={NAV_BUTTON} aria-label={t('budgetPlan.previousYear')} onClick={() => setYear((y) => y - 1)}>
              ‹
            </button>
            <span className="min-w-[4.5rem] text-center text-sm font-medium tabular-nums text-foreground">{year}</span>
            <button className={NAV_BUTTON} aria-label={t('budgetPlan.nextYear')} onClick={() => setYear((y) => y + 1)}>
              ›
            </button>
          </div>
          {/* The empty state has its own, more prominent button. */}
          {canWrite && !(plan && plan.rows.length === 0) && (
            <Button size="sm" className="h-8 gap-1.5" onClick={() => setSuggestOpen(true)}>
              <Sparkles size={13} /> {t('budgetPlan.suggest')}
            </Button>
          )}
        </div>
      }
    />
  )

  const suggestDialog = (
    <BudgetSuggestionDialog
      open={suggestOpen}
      onOpenChange={setSuggestOpen}
      year={year}
      fmt={fmt}
      monthNames={monthNames}
    />
  )

  if (isLoading) {
    return (
      <div>
        {header}
        <div className="space-y-4">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-64 w-full" />
        </div>
      </div>
    )
  }

  if (isError || !plan) {
    return (
      <div>
        {header}
        <p className="py-10 text-center text-sm text-muted-foreground">{t('common.error')}</p>
      </div>
    )
  }

  if (
    plan.rows.length === 0 &&
    plan.income_rows.length === 0 &&
    !(plan.uncategorized_total > 0) &&
    !(plan.uncategorized_income_total > 0)
  ) {
    return (
      <div>
        {header}
        <SectionCard>
          <div className="flex flex-col items-center gap-3 py-12">
            <p className="text-sm text-muted-foreground">{t('budgetPlan.empty')}</p>
            {canWrite && (
              <Button size="sm" className="gap-1.5" onClick={() => setSuggestOpen(true)}>
                <Sparkles size={13} /> {t('budgetPlan.suggest')}
              </Button>
            )}
          </div>
        </SectionCard>
        {suggestDialog}
      </div>
    )
  }

  const left = plan.planned_total - plan.actual_total
  const net = plan.cash_net_total
  const netPlannedToDate = plan.net_planned_by_month.reduce(
    (sum, value, i) => (isFutureMonth(year, i + 1) ? sum : sum + value),
    0,
  )
  const hasNetPlan = plan.income_planned_total > 0 || plan.planned_total > 0

  return (
    <div className="space-y-4">
      {header}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <SummaryCard label={t('budgetPlan.incomeYear')} value={fmt(plan.income_actual_total)} />
        <SummaryCard label={t('budgetPlan.plannedYear')} value={fmt(plan.planned_total)} />
        <SummaryCard label={t('budgetPlan.spentYear')} value={fmt(plan.actual_total)} />
        <SummaryCard
          label={t('budgetPlan.leftYear')}
          value={fmt(left)}
          tone={plan.planned_total > 0 ? (left < 0 ? OVER : UNDER) : undefined}
        />
        <SummaryCard
          label={t('budgetPlan.netYear')}
          value={fmt(net)}
          tone={netTone(net, hasNetPlan ? netPlannedToDate : null)}
          hint={hasNetPlan ? t('budgetPlan.netPlannedToDate', { value: fmt(netPlannedToDate) }) : undefined}
        />
      </div>

      <SectionCard>
        <div className="border-b border-border px-4 py-3 sm:px-5">
          <p className="text-sm font-semibold text-foreground">{t('budgetPlan.chartTitle')}</p>
        </div>
        <div className="h-56 px-2 py-3 sm:px-4">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart
              data={MONTH_NUMBERS.map((m) => ({
                name: monthNames[m - 1],
                planned: plan.planned_by_month[m - 1],
                actual: isFutureMonth(year, m) ? null : plan.actual_by_month[m - 1],
              }))}
              barGap={2}
            >
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
              <XAxis dataKey="name" tick={{ fontSize: 10, fill: 'var(--muted-foreground)' }} axisLine={false} tickLine={false} />
              <YAxis
                tick={privacyMode ? false : { fontSize: 10, fill: 'var(--muted-foreground)' }}
                tickFormatter={(v: number) => money.format(v)}
                axisLine={false}
                tickLine={false}
                width={privacyMode ? 8 : 64}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                cursor={{ fill: 'var(--muted)', opacity: 0.4 }}
                formatter={(v) => fmt(Number(v))}
              />
              <Bar
                dataKey="planned"
                name={t('budgetPlan.planned')}
                fill="var(--muted-foreground)"
                fillOpacity={0.35}
                radius={[3, 3, 0, 0]}
              />
              <Bar dataKey="actual" name={t('budgetPlan.actual')} fill="var(--primary)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </SectionCard>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <Tabs value={view} onValueChange={(v) => setView(v as View)}>
          <TabsList>
            <TabsTrigger value="grid">{t('budgetPlan.viewGrid')}</TabsTrigger>
            <TabsTrigger value="month">{t('budgetPlan.viewMonth')}</TabsTrigger>
          </TabsList>
        </Tabs>

        {view === 'grid' ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant={onlyTotals ? 'default' : 'outline'}
              className="h-8"
              aria-pressed={onlyTotals}
              onClick={() => {
                const all = !onlyTotals
                setCollapsed({ income: all, spending: all, moved: all })
              }}
            >
              {t('budgetPlan.onlyTotals')}
            </Button>
            <Tabs value={metric} onValueChange={(v) => setMetric(v as Metric)}>
              <TabsList>
                <TabsTrigger value="both">{t('budgetPlan.metricBoth')}</TabsTrigger>
                <TabsTrigger value="variance">{t('budgetPlan.difference')}</TabsTrigger>
                <TabsTrigger value="planned">{t('budgetPlan.planned')}</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        ) : (
          <select
            aria-label={t('budgetPlan.month')}
            className="border border-border rounded-lg px-3 py-1.5 text-sm bg-card text-foreground capitalize focus:outline-none focus:ring-2 focus:ring-primary"
            value={month}
            onChange={(e) => setMonth(Number(e.target.value))}
          >
            {MONTH_NUMBERS.map((m) => (
              <option key={m} value={m}>
                {longMonthName(m)}
              </option>
            ))}
          </select>
        )}
      </div>

      {view === 'grid' ? (
        <>
          <IncomeSection
            plan={plan}
            year={year}
            monthNames={monthNames}
            fmt={fmt}
            collapsed={collapsed.income}
            onToggle={() => setCollapsed({ ...collapsed, income: !collapsed.income })}
          />
          <GridView
            plan={plan}
            year={year}
            metric={metric}
            canWrite={canWrite}
            monthNames={monthNames}
            fmt={fmt}
            noBudget={noBudget}
            onSave={(categoryId, m, amount) => saveCell.mutate({ categoryId, month: m, amount })}
            resetKey={saveFailures}
            collapsed={collapsed.spending}
            onToggle={() => setCollapsed({ ...collapsed, spending: !collapsed.spending })}
          />
          {plan.moved_rows.length > 0 && (
            <MovedSection
              plan={plan}
              year={year}
              monthNames={monthNames}
              fmt={fmt}
              collapsed={collapsed.moved}
              onToggle={() => setCollapsed({ ...collapsed, moved: !collapsed.moved })}
            />
          )}
          <SummarySection plan={plan} year={year} monthNames={monthNames} fmt={fmt} noBudget={noBudget} />
        </>
      ) : (
        <MonthView plan={plan} year={year} month={month} fmt={fmt} noBudget={noBudget} />
      )}

      {suggestDialog}
    </div>
  )
}

interface GridViewProps {
  plan: BudgetYearPlan
  year: number
  metric: Metric
  canWrite: boolean
  monthNames: string[]
  fmt: (n: number) => string
  noBudget: string
  onSave: (categoryId: string, month: number, amount: number | null) => void
  resetKey: number
  collapsed?: boolean
  onToggle?: () => void
}

function GridView({
  plan,
  year,
  metric,
  canWrite,
  monthNames,
  fmt,
  noBudget,
  onSave,
  resetKey,
  collapsed,
  onToggle,
}: GridViewProps) {
  const { t } = useTranslation()
  const editing = metric === 'planned' && canWrite
  const futures = MONTH_NUMBERS.map((m) => isFutureMonth(year, m))

  // Sums only what the variance cells show (skips future and unbudgeted months),
  // so the Year column never disagrees with the cells beside it.
  const varianceSum = (cells: Cell[]) => {
    const parts = cells.map((c, i) => cellVariance(c, futures[i])).filter((v): v is number => v != null)
    return parts.length ? parts.reduce((a, b) => a + b, 0) : null
  }

  /** The link of a category's cell; `month` null is the Year column. */
  const linkFor = (row: BudgetYearRow, month: number | null): CellLink => ({
    href: transactionsHref(year, month, row.category_id, 'spending'),
    label: t('budgetPlan.seeTransactions', {
      category: row.category_name,
      month: month == null ? t('budgetPlan.yearColumn') : monthNames[month - 1],
    }),
  })

  const renderCell = (cell: Cell, future: boolean, link?: CellLink) => {
    if (metric === 'variance') return <Variance value={cellVariance(cell, future)} fmt={fmt} />
    if (metric === 'planned') return cell.planned == null ? <Dash /> : fmt(cell.planned)
    return <ActualVsPlan cell={cell} future={future} fmt={fmt} noBudget={noBudget} link={link} />
  }

  const renderYear = (cells: Cell[], plannedTotal: number, actualTotal: number, link?: CellLink) => {
    if (metric === 'variance') return <Variance value={varianceSum(cells)} fmt={fmt} />
    if (metric === 'planned') return fmt(plannedTotal)
    const plannedToDate = cells.reduce((sum, c, i) => (futures[i] ? sum : sum + (c.planned ?? 0)), 0)
    return (
      <ActualVsPlan
        cell={{ planned: plannedTotal || null, actual: actualTotal }}
        future={false}
        fmt={fmt}
        noBudget={noBudget}
        compareTo={plannedToDate}
        link={link}
      />
    )
  }

  const totalCells: Cell[] = MONTH_NUMBERS.map((_, i) => ({
    planned: plan.planned_by_month[i] > 0 ? plan.planned_by_month[i] : null,
    actual: plan.actual_by_month[i],
  }))
  const uncategorized = uncategorizedRow(plan, t('budgetPlan.uncategorized'))
  const displayRows = collapsed ? [] : uncategorized ? [...plan.rows, uncategorized] : plan.rows

  return (
    <SectionCard label={t('budgetPlan.spending')}>
      <SectionHeader title={t('budgetPlan.spending')} collapsed={collapsed} onToggle={onToggle} />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <MonthHeaderRow monthNames={monthNames} first={t('budgets.category')} />
          </thead>
          <tbody>
            {displayRows.map((row) => (
              <tr key={row.category_id} className="border-b border-border hover:bg-muted/50">
                <td className="sticky left-0 z-[1] bg-card py-2 pl-4 pr-2 sm:pl-5">
                  <span className="flex min-w-[9rem] items-center gap-2 text-sm font-medium text-foreground">
                    <CategoryIcon icon={row.category_icon} color={row.category_color} size="sm" />
                    <span className="truncate">{row.category_name}</span>
                  </span>
                </td>
                {row.cells.map((cell, i) => (
                  <td key={cell.month} className="px-1 py-2 text-center tabular-nums">
                    {editing && row.category_id !== UNCATEGORIZED_ID ? (
                      <PlannedInput
                        value={cell.planned}
                        label={t('budgetPlan.cellLabel', { category: row.category_name, month: monthNames[i] })}
                        onCommit={(amount) => onSave(row.category_id, cell.month, amount)}
                        resetKey={resetKey}
                      />
                    ) : (
                      renderCell(cell, futures[i], linkFor(row, cell.month))
                    )}
                  </td>
                ))}
                <td className="px-2 py-2 pr-4 text-center font-semibold tabular-nums sm:pr-5">
                  {renderYear(row.cells, row.planned_total, row.actual_total, linkFor(row, null))}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-muted font-semibold">
              <td className="sticky left-0 z-[1] bg-muted py-2 pl-4 pr-2 text-sm text-foreground sm:pl-5">
                {t('budgetPlan.total')}
              </td>
              {totalCells.map((cell, i) => (
                <td key={i} className="px-1 py-2 text-center tabular-nums">
                  {renderCell(cell, futures[i])}
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center tabular-nums sm:pr-5">
                {renderYear(totalCells, plan.planned_total, plan.actual_total)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      {editing && !collapsed && (
        <p className="border-t border-border px-4 py-2 text-xs text-muted-foreground sm:px-5">
          {t('budgetPlan.clearHint')}
        </p>
      )}
    </SectionCard>
  )
}

interface IncomeSectionProps {
  plan: BudgetYearPlan
  year: number
  monthNames: string[]
  fmt: (n: number) => string
  collapsed?: boolean
  onToggle?: () => void
}

/** What came in against what was expected, coloured the other way round from spending. */
function IncomeCell({
  cell,
  future,
  fmt,
  link,
}: {
  cell: Cell
  future: boolean
  fmt: (n: number) => string
  link?: CellLink
}) {
  if (cell.planned == null && (future || cell.actual === 0)) return <Dash />
  const tone = future ? 'text-muted-foreground' : cell.planned == null ? 'text-foreground' : cell.actual >= cell.planned ? UNDER : SHORT
  return (
    <span className="flex flex-col items-center leading-tight">
      <ActualAmount link={!future && cell.actual !== 0 ? link : undefined} className={`font-medium ${tone}`}>
        {future ? '—' : fmt(cell.actual)}
      </ActualAmount>
      {cell.planned != null && <span className="text-[10px] text-muted-foreground">{fmt(cell.planned)}</span>}
    </span>
  )
}

/** Red when money is going out, amber when behind the plan, green otherwise. */
function netTone(actual: number, planned: number | null) {
  if (actual < 0) return OVER
  return planned != null && actual < planned ? SHORT : UNDER
}

/** A balance: the real one, with the planned one under it. A future month shows only the plan. */
function NetCell({ cell, future, fmt }: { cell: Cell; future: boolean; fmt: (n: number) => string }) {
  if (cell.planned == null && (future || cell.actual === 0)) return <Dash />
  return (
    <span className="flex flex-col items-center leading-tight">
      <span className={future ? 'text-muted-foreground' : netTone(cell.actual, cell.planned)}>
        {future ? '—' : fmt(cell.actual)}
      </span>
      {cell.planned != null && (
        <span className="text-[10px] font-normal text-muted-foreground">{fmt(cell.planned)}</span>
      )}
    </span>
  )
}

/** Income per category and month (planned from recurring income items). */
function IncomeSection({ plan, year, monthNames, fmt, collapsed, onToggle }: IncomeSectionProps) {
  const { t } = useTranslation()
  const futures = MONTH_NUMBERS.map((m) => isFutureMonth(year, m))
  const plannedToDate = (cells: Cell[]) => cells.reduce((sum, c, i) => (futures[i] ? sum : sum + (c.planned ?? 0)), 0)
  const totals: Cell[] = MONTH_NUMBERS.map((_, i) => ({
    planned: plan.income_planned_by_month[i] > 0 ? plan.income_planned_by_month[i] : null,
    actual: plan.income_actual_by_month[i],
  }))
  const td = 'px-1 py-2 text-center tabular-nums'
  const first = 'sticky left-0 z-[1] py-2 pl-4 pr-2 sm:pl-5'
  const uncategorized = uncategorizedRow(plan, t('budgetPlan.uncategorized'), 'income')
  const allRows = uncategorized ? [...plan.income_rows, uncategorized] : plan.income_rows
  const rows = collapsed ? [] : allRows
  const linkFor = (row: BudgetYearRow, month: number | null): CellLink => ({
    href: transactionsHref(year, month, row.category_id, 'income'),
    label: t('budgetPlan.seeTransactions', {
      category: row.category_name,
      month: month == null ? t('budgetPlan.yearColumn') : monthNames[month - 1],
    }),
  })

  return (
    <SectionCard label={t('budgetPlan.income')}>
      <SectionHeader
        title={t('budgetPlan.income')}
        hint={collapsed ? undefined : t('budgetPlan.incomeHint')}
        collapsed={collapsed}
        onToggle={onToggle}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <MonthHeaderRow monthNames={monthNames} first={t('budgets.category')} />
          </thead>
          <tbody>
            {allRows.length === 0 && (
              <tr className="border-b border-border">
                <td colSpan={14} className="px-4 py-4 text-center text-muted-foreground sm:px-5">
                  {t('budgetPlan.noIncome')}
                </td>
              </tr>
            )}
            {rows.map((row) => (
              <tr key={row.category_id} className="border-b border-border hover:bg-muted/50">
                <td className={`${first} bg-card`}>
                  <span className="flex min-w-[9rem] items-center gap-2 text-sm font-medium text-foreground">
                    <CategoryIcon icon={row.category_icon} color={row.category_color} size="sm" />
                    <span className="truncate">{row.category_name}</span>
                  </span>
                </td>
                {row.cells.map((cell, i) => (
                  <td key={cell.month} className={td}>
                    <IncomeCell cell={cell} future={futures[i]} fmt={fmt} link={linkFor(row, cell.month)} />
                  </td>
                ))}
                <td className="px-2 py-2 pr-4 text-center font-semibold tabular-nums sm:pr-5">
                  <IncomeCell
                    cell={{ planned: plannedToDate(row.cells) || null, actual: row.actual_total }}
                    future={false}
                    fmt={fmt}
                    link={linkFor(row, null)}
                  />
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr className="bg-muted font-semibold">
              <td className={`${first} bg-muted text-sm text-foreground`}>{t('budgetPlan.total')}</td>
              {totals.map((cell, i) => (
                <td key={i} className={td}>
                  <IncomeCell cell={cell} future={futures[i]} fmt={fmt} />
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center tabular-nums sm:pr-5">
                <IncomeCell
                  cell={{ planned: plannedToDate(totals) || null, actual: plan.income_actual_total }}
                  future={false}
                  fmt={fmt}
                />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </SectionCard>
  )
}

/** An amount with no plan behind it: plain, a dash when nothing moved. */
function PlainCell({ value, future, fmt, link }: { value: number; future: boolean; fmt: (n: number) => string; link?: CellLink }) {
  if (future || value === 0) return <Dash />
  return (
    <ActualAmount link={link} className={`font-medium ${value < 0 ? UNDER : 'text-foreground'}`}>
      {fmt(value)}
    </ActualAmount>
  )
}

/**
 * Money that left the accounts without being spending: put into investments
 * or sent to a bank Securo does not track. Negative when more came back.
 */
function MovedSection({ plan, year, monthNames, fmt, collapsed, onToggle }: IncomeSectionProps) {
  const { t } = useTranslation()
  const futures = MONTH_NUMBERS.map((m) => isFutureMonth(year, m))
  const td = 'px-1 py-2 text-center tabular-nums'
  const first = 'sticky left-0 z-[1] py-2 pl-4 pr-2 sm:pl-5'
  const linkFor = (row: BudgetYearRow, month: number | null): CellLink => ({
    href: transactionsHref(year, month, row.category_id, 'spending'),
    label: t('budgetPlan.seeTransactions', {
      category: row.category_name,
      month: month == null ? t('budgetPlan.yearColumn') : monthNames[month - 1],
    }),
  })
  return (
    <SectionCard label={t('budgetPlan.moved')}>
      <SectionHeader
        title={t('budgetPlan.moved')}
        hint={collapsed ? undefined : t('budgetPlan.movedHint')}
        collapsed={collapsed}
        onToggle={onToggle}
      />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <MonthHeaderRow monthNames={monthNames} first={t('budgets.category')} />
          </thead>
          <tbody>
            {!collapsed &&
              plan.moved_rows.map((row) => (
                <tr key={row.category_id} className="border-b border-border hover:bg-muted/50">
                  <td className={`${first} bg-card`}>
                    <span className="flex min-w-[9rem] items-center gap-2 text-sm font-medium text-foreground">
                      <CategoryIcon icon={row.category_icon} color={row.category_color} size="sm" />
                      <span className="truncate">{row.category_name}</span>
                    </span>
                  </td>
                  {row.cells.map((cell, i) => (
                    <td key={cell.month} className={td}>
                      <PlainCell value={cell.actual} future={futures[i]} fmt={fmt} link={linkFor(row, cell.month)} />
                    </td>
                  ))}
                  <td className="px-2 py-2 pr-4 text-center font-semibold tabular-nums sm:pr-5">
                    <PlainCell value={row.actual_total} future={false} fmt={fmt} link={linkFor(row, null)} />
                  </td>
                </tr>
              ))}
          </tbody>
          <tfoot>
            <tr className="bg-muted font-semibold">
              <td className={`${first} bg-muted text-sm text-foreground`}>{t('budgetPlan.total')}</td>
              {plan.moved_by_month.map((value, i) => (
                <td key={i} className={td}>
                  <PlainCell value={value} future={futures[i]} fmt={fmt} />
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center tabular-nums sm:pr-5">
                <PlainCell value={plan.moved_total} future={false} fmt={fmt} />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </SectionCard>
  )
}

/**
 * The quick glance: what came in, what went out, what moved to investments
 * or other banks, and what that left in the accounts, month by month and
 * added up through the year, each against the plan.
 */
function SummarySection({
  plan,
  year,
  monthNames,
  fmt,
  noBudget,
}: {
  plan: BudgetYearPlan
  year: number
  monthNames: string[]
  fmt: (n: number) => string
  noBudget: string
}) {
  const { t } = useTranslation()
  const futures = MONTH_NUMBERS.map((m) => isFutureMonth(year, m))
  const plannedToDate = (cells: Cell[]) => cells.reduce((sum, c, i) => (futures[i] ? sum : sum + (c.planned ?? 0)), 0)
  const td = 'px-1 py-2 text-center tabular-nums'
  const first = 'sticky left-0 z-[1] bg-card py-2 pl-4 pr-2 text-sm text-foreground sm:pl-5'
  const income: Cell[] = MONTH_NUMBERS.map((_, i) => ({
    planned: plan.income_planned_by_month[i] > 0 ? plan.income_planned_by_month[i] : null,
    actual: plan.income_actual_by_month[i],
  }))
  const spending: Cell[] = MONTH_NUMBERS.map((_, i) => ({
    planned: plan.planned_by_month[i] > 0 ? plan.planned_by_month[i] : null,
    actual: plan.actual_by_month[i],
  }))
  // The balance in the accounts against the plan (planned income minus
  // budgets), and the same accumulated through the year.
  const hasNetPlan = plan.income_planned_total > 0 || plan.planned_total > 0
  const nets: Cell[] = MONTH_NUMBERS.map((_, i) => ({
    actual: plan.cash_net_by_month[i],
    // A month with nothing planned has no plan to compare against.
    planned: plan.income_planned_by_month[i] > 0 || plan.planned_by_month[i] > 0 ? plan.net_planned_by_month[i] : null,
  }))
  const accumulated: Cell[] = nets.map((_, i) => {
    const soFar = nets.slice(0, i + 1)
    return {
      actual: soFar.reduce((sum, c, j) => (futures[j] ? sum : sum + c.actual), 0),
      planned: soFar.some((c) => c.planned != null) ? soFar.reduce((sum, c) => sum + (c.planned ?? 0), 0) : null,
    }
  })
  const spendingToDate = plannedToDate(spending)
  return (
    <SectionCard label={t('budgetPlan.summary')}>
      <SectionHeader title={t('budgetPlan.summary')} hint={t('budgetPlan.summaryHint')} />
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <MonthHeaderRow monthNames={monthNames} first="" />
          </thead>
          <tbody>
            <tr className="border-b border-border">
              <td className={first}>{t('budgetPlan.income')}</td>
              {income.map((cell, i) => (
                <td key={i} className={td}>
                  <IncomeCell cell={cell} future={futures[i]} fmt={fmt} />
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center font-semibold tabular-nums sm:pr-5">
                <IncomeCell
                  cell={{ planned: plannedToDate(income) || null, actual: plan.income_actual_total }}
                  future={false}
                  fmt={fmt}
                />
              </td>
            </tr>
            <tr className="border-b border-border">
              <td className={first}>{t('budgetPlan.spending')}</td>
              {spending.map((cell, i) => (
                <td key={i} className={td}>
                  <ActualVsPlan cell={cell} future={futures[i]} fmt={fmt} noBudget={noBudget} />
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center font-semibold tabular-nums sm:pr-5">
                <ActualVsPlan
                  cell={{ planned: plan.planned_total || null, actual: plan.actual_total }}
                  future={false}
                  fmt={fmt}
                  noBudget={noBudget}
                  compareTo={spendingToDate}
                />
              </td>
            </tr>
            <tr className="border-b border-border">
              <td className={first} title={t('budgetPlan.movedHint')}>{t('budgetPlan.summaryMoved')}</td>
              {plan.moved_by_month.map((value, i) => (
                <td key={i} className={td}>
                  <PlainCell value={value} future={futures[i]} fmt={fmt} />
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center font-semibold tabular-nums sm:pr-5">
                <PlainCell value={plan.moved_total} future={false} fmt={fmt} />
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr className="border-t border-border bg-muted font-semibold">
              <td className={`${first} bg-muted`} title={t('budgetPlan.netHint')}>
                {t('budgetPlan.net')}
              </td>
              {nets.map((cell, i) => (
                <td key={i} className={td}>
                  <NetCell cell={cell} future={futures[i]} fmt={fmt} />
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center tabular-nums sm:pr-5">
                <NetCell
                  cell={{ actual: plan.cash_net_total, planned: hasNetPlan ? plannedToDate(nets) : null }}
                  future={false}
                  fmt={fmt}
                />
              </td>
            </tr>
            <tr className="border-t border-border font-semibold">
              <td className={first} title={t('budgetPlan.netAccumulatedHint')}>
                {t('budgetPlan.netAccumulated')}
              </td>
              {accumulated.map((cell, i) => (
                <td key={i} className={td}>
                  <NetCell cell={cell} future={futures[i]} fmt={fmt} />
                </td>
              ))}
              <td className="px-2 py-2 pr-4 text-center tabular-nums sm:pr-5">
                <NetCell
                  cell={{ actual: plan.cash_net_total, planned: hasNetPlan ? plan.net_planned_total : null }}
                  future={false}
                  fmt={fmt}
                />
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </SectionCard>
  )
}

interface MonthViewProps {
  plan: BudgetYearPlan
  year: number
  month: number
  fmt: (n: number) => string
  noBudget: string
}

function MonthView({ plan, year, month, fmt, noBudget }: MonthViewProps) {
  const { t, i18n } = useTranslation()
  const future = isFutureMonth(year, month)
  const i = month - 1
  const monthLabel = new Intl.DateTimeFormat(i18n.language, { month: 'short' }).format(new Date(year, month - 1, 1))

  // Only categories that have a budget or spending in this month.
  const uncategorized = uncategorizedRow(plan, t('budgetPlan.uncategorized'))
  const rows = (uncategorized ? [...plan.rows, uncategorized] : plan.rows)
    .map((r) => ({ row: r, cell: r.cells[i] }))
    .filter(({ cell }) => cell.planned != null || cell.actual > 0)
    .sort((a, b) => b.cell.actual - a.cell.actual || (b.cell.planned ?? 0) - (a.cell.planned ?? 0))

  const totalPlanned = plan.planned_by_month[i]
  const totalActual = plan.actual_by_month[i]
  const totalPlannedOrNull = totalPlanned > 0 ? totalPlanned : null
  const totalPct = future ? null : percentUsed(totalPlannedOrNull, totalActual)
  const th = 'py-3 text-xs font-medium text-muted-foreground'

  if (rows.length === 0) {
    return (
      <SectionCard>
        <p className="py-10 text-center text-sm text-muted-foreground">{t('budgetPlan.monthEmpty')}</p>
      </SectionCard>
    )
  }

  return (
    <SectionCard>
      <div className="overflow-x-auto">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className={`${th} pl-4 text-left sm:pl-5`}>{t('budgets.category')}</th>
              <th className={`${th} text-right`}>{t('budgetPlan.planned')}</th>
              <th className={`${th} text-right`}>{t('budgetPlan.actual')}</th>
              <th className={`${th} text-right`}>{t('budgetPlan.difference')}</th>
              <th className={`${th} w-48 pl-4 pr-4 text-left sm:pr-5`}>
                <span className="sr-only">{t('budgetPlan.progress')}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ row, cell }) => {
              const pct = future ? null : percentUsed(cell.planned, cell.actual)
              const over = pct != null && pct > 100
              return (
                <tr key={row.category_id} className="border-b border-border last:border-0 hover:bg-muted/50">
                  <td className="py-3 pl-4 text-sm font-medium text-foreground sm:pl-5">
                    <span className="flex items-center gap-2">
                      <CategoryIcon icon={row.category_icon} color={row.category_color} size="sm" />
                      <span>{row.category_name}</span>
                    </span>
                  </td>
                  <td className="py-3 text-right text-sm tabular-nums text-foreground">
                    {cell.planned == null ? <span className="text-muted-foreground">{noBudget}</span> : fmt(cell.planned)}
                  </td>
                  <td className="py-3 text-right text-sm font-medium tabular-nums text-foreground">
                    {future ? (
                      <Dash />
                    ) : (
                      <ActualAmount
                        className=""
                        link={
                          cell.actual > 0
                            ? {
                                href: transactionsHref(year, month, row.category_id, 'spending'),
                                label: t('budgetPlan.seeTransactions', {
                                  category: row.category_name,
                                  month: monthLabel,
                                }),
                              }
                            : undefined
                        }
                      >
                        {fmt(cell.actual)}
                      </ActualAmount>
                    )}
                  </td>
                  <td className="py-3 text-right text-sm tabular-nums">
                    <Variance value={cellVariance(cell, future)} fmt={fmt} />
                  </td>
                  <td className="py-3 pl-4 pr-4 sm:pr-5">
                    {pct != null && (
                      <div className="flex items-center gap-2">
                        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                          <div
                            className={`h-full rounded-full ${over ? 'bg-rose-500' : 'bg-emerald-500'}`}
                            style={{ width: `${Math.min(100, pct)}%` }}
                          />
                        </div>
                        <span className={`w-24 text-right text-xs tabular-nums ${over ? OVER : 'text-muted-foreground'}`}>
                          {t('budgetPlan.percentOfPlan', { percent: pct })}
                        </span>
                      </div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr className="bg-muted/40 font-semibold">
              <td className="py-3 pl-4 text-sm text-foreground sm:pl-5">{t('budgetPlan.total')}</td>
              <td className="py-3 text-right text-sm tabular-nums">{fmt(totalPlanned)}</td>
              <td className="py-3 text-right text-sm tabular-nums">{future ? <Dash /> : fmt(totalActual)}</td>
              <td className="py-3 text-right text-sm tabular-nums">
                <Variance value={cellVariance({ planned: totalPlannedOrNull, actual: totalActual }, future)} fmt={fmt} />
              </td>
              <td className="py-3 pl-4 pr-4 text-right text-xs font-normal tabular-nums text-muted-foreground sm:pr-5">
                {totalPct != null ? t('budgetPlan.percentOfPlan', { percent: totalPct }) : null}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
    </SectionCard>
  )
}
