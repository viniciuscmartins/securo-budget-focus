// The fork's grouped sidebar accounts (components/sidebar-account-groups.tsx)
// as wired into upstream's app-layout.tsx, which only carries one-line hooks.
//
// Upstream's own account list stays in app-layout.tsx, switched off by
// FORK_SIDEBAR_ACCOUNTS rather than deleted: an upstream change inside it
// then merges without a conflict, and type-checking keeps it valid.
import { useQuery } from '@tanstack/react-query'
import { monthOutlook as monthOutlookApi } from '@/fork/api'
import { sidebarGroupTotals } from '@/lib/sidebar-account-totals'
import type { Account } from '@/types'

/** The fork's grouped list replaces upstream's flat one. */
export const FORK_SIDEBAR_ACCOUNTS: boolean = true

export function useForkSidebar(accountsList: Account[] | undefined, visibleAccounts: Account[]) {
  // Each card's next bill. Under the 'accounts' key so that whatever refreshes
  // the account list (a new transaction, a payment) refreshes the bills too.
  const { data: cardBills } = useQuery({
    queryKey: ['accounts', 'card-bills'],
    queryFn: () => monthOutlookApi.cardBills(),
    enabled: (accountsList ?? []).some((a) => a.type === 'credit_card'),
  })
  // The header adds up the two groups below it: money in the accounts minus
  // the cards' next bills (each card's full debt shows on its own row).
  const total = sidebarGroupTotals(visibleAccounts, cardBills).total
  return { cardBills, total }
}
