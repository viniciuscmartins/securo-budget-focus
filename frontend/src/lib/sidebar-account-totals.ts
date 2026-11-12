import { sidebarAccountBalance } from '@/lib/account-utils'
import type { Account, MonthOutlookCardBill } from '@/types'

/** A card's amount in the sidebar: its next bill to pay, or its total until the bills load. */
export function cardAmountOf(account: Account, cardBills: MonthOutlookCardBill[] | undefined): number {
  if (!cardBills) return sidebarAccountBalance(account).amount
  const bill = cardBills.find((b) => b.account_id === account.id)
  return bill ? -bill.amount : 0
}

/**
 * The subtotals the groups show, and the header total as their sum: money in
 * the accounts minus the cards' next bills.
 */
export function sidebarGroupTotals(accounts: Account[], cardBills: MonthOutlookCardBill[] | undefined) {
  let cash = 0
  let cards = 0
  for (const account of accounts) {
    if (account.type === 'credit_card') cards += cardAmountOf(account, cardBills)
    else cash += sidebarAccountBalance(account).amount
  }
  return { cash, cards, total: cash + cards }
}
