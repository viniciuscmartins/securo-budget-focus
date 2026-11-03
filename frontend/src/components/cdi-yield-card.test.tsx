import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { CdiYieldCard } from '@/components/cdi-yield-card'
import { renderWithProviders, t } from '@/test/utils'
import type { CdiYieldSettings } from '@/types'

const api = vi.hoisted(() => ({
  get: vi.fn(),
  save: vi.fn(),
  recalculate: vi.fn(),
  remove: vi.fn(),
}))

vi.mock('@/lib/api', () => ({
  cdiYield: api,
  timezones: { list: async () => ({ default: 'UTC', available: [] }) },
}))
vi.mock('@/hooks/use-display-locale', () => ({
  useDisplayLocale: () => 'en-US',
  useDateLocale: () => 'en-US',
}))

const off: CdiYieldSettings = {
  configured: false,
  enabled: false,
  percent: null,
  mode: null,
  start_date: null,
  last_posted_date: null,
  total_posted: 0,
}
const on: CdiYieldSettings = {
  configured: true,
  enabled: true,
  percent: 105,
  mode: 'calendar_days',
  start_date: '2026-01-01',
  last_posted_date: '2026-11-02',
  total_posted: 1234.56,
}

function render(canWrite = true) {
  return renderWithProviders(
    <CdiYieldCard accountId="acc-1" currency="BRL" locale="en-US" dateLocale="en-US" mask={(v) => v} canWrite={canWrite} />,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.save.mockResolvedValue({ posted_days: 3, settings: on })
  api.recalculate.mockResolvedValue({ posted_days: 3, settings: on })
  api.remove.mockResolvedValue(undefined)
})

describe('CdiYieldCard', () => {
  it('explains the feature and offers set-up when it is off', async () => {
    api.get.mockResolvedValue(off)
    render()
    expect(await screen.findByText(t('cdiYield.notConfigured'))).toBeInTheDocument()
    expect(screen.getByRole('button', { name: t('cdiYield.setUp') })).toBeInTheDocument()
  })

  it('saves percent, mode and start date', async () => {
    api.get.mockResolvedValue(off)
    const { user } = render()
    await user.click(await screen.findByRole('button', { name: t('cdiYield.setUp') }))

    const percent = screen.getByLabelText(t('cdiYield.percentLabel'))
    await user.clear(percent)
    await user.type(percent, '102,5')
    await user.selectOptions(screen.getByLabelText(t('cdiYield.accrual')), 'calendar_days')
    expect(screen.getByText(t('cdiYield.calendarDaysHint'))).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: t('common.save') }))

    await waitFor(() => expect(api.save).toHaveBeenCalledTimes(1))
    const [accountId, body] = api.save.mock.calls[0]
    expect(accountId).toBe('acc-1')
    expect(body.percent).toBe(102.5)
    expect(body.mode).toBe('calendar_days')
    expect(body.start_date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
  })

  it('will not save an invalid percentage', async () => {
    api.get.mockResolvedValue(off)
    const { user } = render()
    await user.click(await screen.findByRole('button', { name: t('cdiYield.setUp') }))
    const percent = screen.getByLabelText(t('cdiYield.percentLabel'))
    await user.clear(percent)
    await user.type(percent, '400')
    expect(screen.getByRole('button', { name: t('common.save') })).toBeDisabled()
  })

  it('summarises a configured account and recalculates on request', async () => {
    api.get.mockResolvedValue(on)
    const { user } = render()
    expect(await screen.findByText(t('cdiYield.percentOfCdi', { percent: 105 }))).toBeInTheDocument()
    expect(screen.getByText(t('cdiYield.calendarDays'))).toBeInTheDocument()
    expect(screen.getByText(/1,234\.56/)).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: t('cdiYield.recalculate') }))
    await waitFor(() => expect(api.recalculate).toHaveBeenCalledWith('acc-1'))
  })

  it('asks whether to keep posted yield when turning off', async () => {
    api.get.mockResolvedValue(on)
    const { user } = render()
    await user.click(await screen.findByText(t('cdiYield.turnOff')))
    await user.click(screen.getByRole('button', { name: t('cdiYield.keepPosted') }))
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith('acc-1', false))
  })

  it('is read-only without write access', async () => {
    api.get.mockResolvedValue(on)
    render(false)
    await screen.findByText(t('cdiYield.percentOfCdi', { percent: 105 }))
    expect(screen.queryByRole('button', { name: t('cdiYield.recalculate') })).not.toBeInTheDocument()
    expect(screen.queryByText(t('cdiYield.turnOff'))).not.toBeInTheDocument()
  })
})
