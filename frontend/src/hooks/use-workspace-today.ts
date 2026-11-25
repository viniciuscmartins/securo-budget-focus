import { useEffectiveTimezone } from '@/hooks/use-timezone'
import { todayInTimezone } from '@/lib/date-utils'

/**
 * Today in the timezone the workspace keeps its books in, as a local Date at
 * midnight (fork). The server dates the plan and the yield by that timezone,
 * so the page must not use the browser's clock: near midnight at a month's
 * end the two can disagree on the current month.
 */
export function useWorkspaceToday(): Date {
  const [year, month, day] = todayInTimezone(useEffectiveTimezone()).split('-').map(Number)
  return new Date(year, month - 1, day)
}
