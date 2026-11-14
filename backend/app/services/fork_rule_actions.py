"""The fork's rule actions: "transfer_to_account" and "link_recurring".

Upstream's rule_service calls into this module from a few one-line hooks
(validation, apply, preview), so upstream merges don't touch the fork's logic.
Both actions need the database (they create a counterpart or move a schedule
along), so they run after upstream's in-memory actions, once per transaction.
See docs/fork/REFACTOR-PLAN.md.
"""

import uuid
from typing import TYPE_CHECKING, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.models.transaction import Transaction


TRANSFER_ACTION = "transfer_to_account"


def transfer_target(actions: list) -> uuid.UUID | None:
    """The account a rule's "transfer to account" action points at, if any.

    The first well-formed one wins; a rule only ever moves a transaction once.
    """
    for action in actions or []:
        op = action.get("op") if isinstance(action, dict) else getattr(action, "op", None)
        if op != TRANSFER_ACTION:
            continue
        value = action.get("value") if isinstance(action, dict) else getattr(action, "value", None)
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            continue
    return None


def transfer_would_apply(tx: "Transaction", to_account_id: uuid.UUID | None) -> bool:
    """Whether `transfer_by_rule` would create a counterpart for `tx`.

    Mirrors its row checks (the destination itself is validated when the rule
    is saved), so a rule preview can count these rows as changing.
    """
    if to_account_id is None:
        return False
    return (
        not getattr(tx, "is_ignored", False)
        and getattr(tx, "transfer_pair_id", None) is None
        and getattr(tx, "source", None) not in {"opening_balance", "transfer"}
        and getattr(tx, "account_id", None) != to_account_id
    )


LINK_RECURRING_ACTION = "link_recurring"


def recurring_target(actions: list) -> uuid.UUID | None:
    """The recurring item a rule's "link to recurring" action points at, if any.

    The first well-formed one wins; a transaction belongs to one schedule.
    """
    for action in actions or []:
        op = action.get("op") if isinstance(action, dict) else getattr(action, "op", None)
        if op != LINK_RECURRING_ACTION:
            continue
        value = action.get("value") if isinstance(action, dict) else getattr(action, "value", None)
        try:
            return uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            continue
    return None


def link_would_apply(
    tx: "Transaction", recurring_id: uuid.UUID | None, recurring_type: str | None = None
) -> bool:
    """Whether `recurring_link_service.link_by_rule` would link `tx`.

    Mirrors its row checks so a rule preview can count these rows as
    changing. `recurring_type` is the schedule's direction when the caller
    knows it; a credit never pays a debit schedule.
    """
    if recurring_id is None:
        return False
    if recurring_type is not None and getattr(tx, "type", None) != recurring_type:
        return False
    return (
        not getattr(tx, "is_ignored", False)
        and getattr(tx, "recurring_transaction_id", None) is None
        and getattr(tx, "source", None) not in {"opening_balance", "recurring"}
    )


# Accepted as `RuleAction.op` on top of upstream's set_category, set_payee,
# set_description, append_notes and ignore (rule_service._ALLOWED_ACTION_OPS).
FORK_ACTION_OPS = frozenset({TRANSFER_ACTION, LINK_RECURRING_ACTION})


def _item_value(item, key: str):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


async def validate_action(
    session: AsyncSession, workspace_id: uuid.UUID, op: str, value
) -> None:
    """Check one fork action of a rule being saved; raises ValueError."""
    if op == TRANSFER_ACTION:
        from app.models.account import Account

        # Only into a manual, open account of this workspace: a synced
        # account gets its own real copy of the movement from the bank,
        # so a counterpart there would count the money twice.
        try:
            account_id = uuid.UUID(str(value))
        except (TypeError, ValueError):
            raise ValueError("Account not found")
        account = await session.get(Account, account_id)
        if account is None or account.workspace_id != workspace_id:
            raise ValueError("Account not found")
        if account.connection_id is not None:
            raise ValueError("Transfers by rule can only go to a manual account")
        if account.is_closed:
            raise ValueError("Account is closed")

    elif op == LINK_RECURRING_ACTION:
        from app.models.recurring_transaction import RecurringTransaction

        try:
            recurring_id = uuid.UUID(str(value))
        except (TypeError, ValueError):
            raise ValueError("Recurring item not found")
        recurring = await session.get(RecurringTransaction, recurring_id)
        if recurring is None or recurring.workspace_id != workspace_id:
            raise ValueError("Recurring item not found")


def validate_counts(actions: list) -> None:
    """A rule carries at most one of each fork action."""
    if sum(1 for a in actions or [] if _item_value(a, "op") == TRANSFER_ACTION) > 1:
        raise ValueError("A rule can transfer to only one account")
    if sum(1 for a in actions or [] if _item_value(a, "op") == LINK_RECURRING_ACTION) > 1:
        raise ValueError("A rule can link to only one recurring item")


async def transfer_by_rule(
    session: AsyncSession, transaction: "Transaction", to_account_id: uuid.UUID
) -> bool:
    """Local import: transaction_service imports rule_service at load time."""
    from app.services.rule_transfer_service import transfer_by_rule as _transfer_by_rule

    return await _transfer_by_rule(session, transaction, to_account_id)


async def link_by_rule(
    session: AsyncSession, transaction: "Transaction", recurring_id: uuid.UUID
) -> bool:
    """Local import: keeps the recurring link service out of rule_service's load."""
    from app.services.recurring_link_service import link_by_rule as _link_by_rule

    return await _link_by_rule(session, transaction, recurring_id)


class Targets:
    """The fork actions of the rules that matched one transaction.

    The highest-priority rule wins, like categories do: call `collect` for
    each matching rule in priority order, then `apply` once, after every rule
    has run, so an `ignore` from any rule is respected.
    """

    def __init__(self, actions: Optional[list] = None) -> None:
        self.transfer_to: Optional[uuid.UUID] = None
        self.link_to: Optional[uuid.UUID] = None
        if actions is not None:
            self.collect(actions)

    def collect(self, actions: list) -> None:
        self.transfer_to = self.transfer_to or transfer_target(actions)
        self.link_to = self.link_to or recurring_target(actions)

    async def apply(self, session: AsyncSession, transaction: "Transaction") -> bool:
        """Carry out the collected actions. Returns True if any changed the
        transaction. Flushes, never commits."""
        transferred = linked = False
        if self.transfer_to is not None:
            transferred = await transfer_by_rule(session, transaction, self.transfer_to)
        if self.link_to is not None and getattr(transaction, "workspace_id", None) is not None:
            linked = await link_by_rule(session, transaction, self.link_to)
        return transferred or linked


async def preview_check(
    session: AsyncSession, actions: list
) -> Callable[["Transaction"], bool]:
    """For a rule preview: whether the fork actions would change a row."""
    transfer_to = transfer_target(actions)
    link_to = recurring_target(actions)
    link_type = None
    if link_to is not None:
        from app.services.recurring_link_service import recurring_type

        link_type = await recurring_type(session, link_to)

    def would_change(draft: "Transaction") -> bool:
        return transfer_would_apply(draft, transfer_to) or link_would_apply(
            draft, link_to, link_type
        )

    return would_change
