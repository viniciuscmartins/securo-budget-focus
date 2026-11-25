import { afterEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'

import { useWorkspaceToday } from '@/hooks/use-workspace-today'
import { isFutureMonth } from '@/lib/budget-plan-utils'

const tz = vi.hoisted(() => ({ value: 'America/Sao_Paulo' as string | undefined }))
vi.mock('@/hooks/use-timezone', () => ({ useEffectiveTimezone: () => tz.value }))

describe('useWorkspaceToday', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('is still the last day of the month in the workspace when UTC has moved on', () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(new Date('2026-02-01T01:30:00Z')) // 22:30 on 31 Jan in Sao Paulo
    const { result } = renderHook(() => useWorkspaceToday())
    expect([result.current.getFullYear(), result.current.getMonth() + 1, result.current.getDate()]).toEqual([2026, 1, 31])
    // So February is still ahead in the plan.
    expect(isFutureMonth(2026, 2, result.current)).toBe(true)
  })
})
