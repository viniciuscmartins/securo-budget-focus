import { useQuery } from '@tanstack/react-query'

import { recurringLinks } from '@/fork/api'

/** Ids of the recurring items whose amount follows the average of their payments. */
export function useRecurringAmountModes(enabled = true) {
  const { data } = useQuery({
    queryKey: ['recurring', 'amount-modes'],
    queryFn: () => recurringLinks.amountModes(),
    enabled,
  })
  return new Set((data ?? []).map((m) => m.recurring_id))
}
