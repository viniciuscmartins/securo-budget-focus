"""Link real transactions to recurring items, and keep variable amounts honest.

Three things live here, all for bills paid by boleto or Pix (or a salary)
that the built-in matcher in `recurring_match_service` misses because the
value changes or the bank's description looks nothing like the bill's name:

- **Make recurring from a transaction.** Any existing transaction, synced
  ones included, becomes the first occurrence of a new recurring item that
  copies its account, amount, category and day of the month. Optionally a
  rule is created with it, so later payments link themselves.
- **The "link_recurring" rule action.** A rule (payee, description, notes...)
  decides which transactions pay a recurring item, instead of an exact
  amount match. Linking also replaces the occurrence's generated placeholder
  and moves the schedule past the occurrence it paid.
- **Amount from the average of the last payments.** A recurring item can
  follow the average of its last three linked payments, so the expected
  value tracks a school fee or gym price that changes. The amount stays
  editable; turning the option off keeps whatever value it has.

Settings live in `app_settings` under `recurring_amount:<recurring id>`, so
the feature needs no migration of its own (same approach as CDI yield).
"""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Literal, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.app_clock import app_today
from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.recurring_transaction import RecurringTransaction
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.services import category_line_service, recurring_match_service
from app.services.fork_rule_actions import LINK_RECURRING_ACTION
from app.services.fx_rate_service import stamp_primary_amount

logger = logging.getLogger(__name__)

SETTING_PREFIX = "recurring_amount:"
DEFAULT_WINDOW = 3
CENT = Decimal("0.01")

Frequency = Literal["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"]

# Half of each period, in days: how far from an occurrence a payment may sit
# and still count as paying it (a monthly fee paid up to two weeks early or
# late). Used to find the occurrence's placeholder and to decide whether a
# payment is the one now due, rather than an old one linked retroactively.
_HALF_PERIOD_DAYS = {
    "weekly": 3,
    "biweekly": 7,
    "monthly": 15,
    "quarterly": 45,
    "semiannual": 90,
    "yearly": 180,
}

# Sources that are not real money movements: never linked, never averaged.
_NOT_REAL = ("recurring", "opening_balance")


class RecurringLinkError(ValueError):
    """A request these endpoints can't honour (HTTP 400)."""


class RecurringLinkNotFound(RecurringLinkError):
    """No such transaction or recurring item in this workspace (HTTP 404)."""


# ---------------------------------------------------------------------------
# Amount mode settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AmountMode:
    """How a recurring item's expected amount is kept up to date."""

    mode: Literal["average"] = "average"
    window: int = DEFAULT_WINDOW

    def to_json(self) -> str:
        return json.dumps({"mode": self.mode, "window": self.window})

    @staticmethod
    def from_json(raw: str) -> Optional["AmountMode"]:
        try:
            data = json.loads(raw)
            window = int(data.get("window", DEFAULT_WINDOW))
            mode = data.get("mode", "average")
        except (ValueError, TypeError, AttributeError):
            return None
        if mode != "average" or not 1 <= window <= 12:
            return None
        return AmountMode(mode=mode, window=window)


def setting_key(recurring_id: uuid.UUID) -> str:
    return f"{SETTING_PREFIX}{recurring_id}"


async def get_amount_mode(session: AsyncSession, recurring_id: uuid.UUID) -> Optional[AmountMode]:
    row = await session.get(AppSetting, setting_key(recurring_id))
    return AmountMode.from_json(row.value) if row is not None else None


async def get_recurring(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID
) -> RecurringTransaction:
    recurring = await session.get(RecurringTransaction, recurring_id)
    if recurring is None or recurring.workspace_id != workspace_id:
        raise RecurringLinkNotFound("Recurring item not found")
    return recurring


async def amount_modes(
    session: AsyncSession, workspace_id: uuid.UUID
) -> dict[uuid.UUID, AmountMode]:
    """Every recurring item of the workspace that follows its payments."""
    ids = (
        (
            await session.execute(
                select(RecurringTransaction.id).where(
                    RecurringTransaction.workspace_id == workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not ids:
        return {}
    rows = (
        (
            await session.execute(
                select(AppSetting).where(AppSetting.key.in_([setting_key(i) for i in ids]))
            )
        )
        .scalars()
        .all()
    )
    modes: dict[uuid.UUID, AmountMode] = {}
    for row in rows:
        mode = AmountMode.from_json(row.value)
        if mode is not None:
            modes[uuid.UUID(row.key[len(SETTING_PREFIX) :])] = mode
    return modes


async def set_amount_mode(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    recurring_id: uuid.UUID,
    mode: Optional[AmountMode],
) -> RecurringTransaction:
    """Turn the average on (and apply it now) or off. Commits.

    Off keeps the current amount, so the person can then type their own.
    """
    recurring = await get_recurring(session, workspace_id, recurring_id)
    row = await session.get(AppSetting, setting_key(recurring_id))
    if mode is None:
        if row is not None:
            await session.delete(row)
    else:
        if row is None:
            session.add(AppSetting(key=setting_key(recurring_id), value=mode.to_json()))
        else:
            row.value = mode.to_json()
        await refresh_amount(session, recurring, mode)
    await session.commit()
    await session.refresh(recurring)
    return recurring


async def recent_payments(
    session: AsyncSession, recurring: RecurringTransaction, window: int
) -> list[Transaction]:
    """The latest real payments linked to a recurring item, newest first.

    Only payments in the item's own currency: an average across currencies
    would mean nothing.
    """
    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.recurring_transaction_id == recurring.id,
            Transaction.source.not_in(_NOT_REAL),
            Transaction.is_ignored == False,  # noqa: E712
            Transaction.currency == recurring.currency,
            Transaction.type == recurring.type,
        )
        .order_by(Transaction.date.desc(), Transaction.created_at.desc())
        .limit(window)
    )
    return list(result.scalars().all())


def average(amounts: list[Decimal]) -> Optional[Decimal]:
    if not amounts:
        return None
    total = sum((Decimal(str(a)) for a in amounts), Decimal("0"))
    return (total / len(amounts)).quantize(CENT, rounding=ROUND_HALF_EVEN)


async def refresh_amount(
    session: AsyncSession,
    recurring: RecurringTransaction,
    mode: Optional[AmountMode] = None,
) -> bool:
    """Set the expected amount to the average of the last payments.

    Returns whether the amount changed. Nothing changes without at least one
    linked payment. Does not commit.
    """
    mode = mode or await get_amount_mode(session, recurring.id)
    if mode is None:
        return False
    payments = await recent_payments(session, recurring, mode.window)
    new_amount = average([p.amount for p in payments])
    if new_amount is None or new_amount <= 0 or new_amount == recurring.amount:
        return False
    old_amount = recurring.amount
    recurring.amount = new_amount
    # A recurring item split across categories keeps its parts in step.
    await category_line_service.rescale_template_lines(session, recurring, old_amount)
    await stamp_primary_amount(session, recurring.user_id, recurring, date_field="start_date")
    return True


async def refresh_all(session: AsyncSession) -> int:
    """Periodic job: re-average every recurring item that follows its payments.

    Catches payments linked outside this module (the built-in matcher during
    a bank sync, a suggestion accepted by hand). Settings of recurring items
    that no longer exist are cleaned up. Commits.
    """
    rows = (
        (await session.execute(select(AppSetting).where(AppSetting.key.startswith(SETTING_PREFIX))))
        .scalars()
        .all()
    )
    changed = 0
    for row in rows:
        try:
            recurring_id = uuid.UUID(row.key[len(SETTING_PREFIX) :])
        except ValueError:
            continue
        recurring = await session.get(RecurringTransaction, recurring_id)
        if recurring is None:
            await session.delete(row)
            continue
        mode = AmountMode.from_json(row.value)
        if mode is None:
            continue
        if await refresh_amount(session, recurring, mode):
            changed += 1
    await session.commit()
    return changed


# ---------------------------------------------------------------------------
# Linking
# ---------------------------------------------------------------------------


def half_period(frequency: str) -> timedelta:
    return timedelta(days=_HALF_PERIOD_DAYS.get(frequency, 15))


async def _placeholder_for(
    session: AsyncSession, recurring: RecurringTransaction, around: date
) -> Optional[Transaction]:
    """The generated placeholder of the occurrence a payment on `around` pays."""
    reach = half_period(recurring.frequency)
    result = await session.execute(
        select(Transaction).where(
            Transaction.recurring_transaction_id == recurring.id,
            Transaction.source == "recurring",
            Transaction.date >= around - reach,
            Transaction.date <= around + reach,
        )
    )
    candidates = list(result.scalars().all())
    if not candidates:
        return None
    return min(candidates, key=lambda p: abs((p.date - around).days))


async def link(
    session: AsyncSession, transaction: Transaction, recurring: RecurringTransaction
) -> bool:
    """Make `transaction` a payment of `recurring`. Does not commit.

    - The occurrence's generated placeholder, if there is one, is removed:
      the real payment now stands for it. Its category fills an empty one.
    - A recurring item split across categories gives an uncategorized
      payment its parts, sized to the payment, as upstream's own matchers
      do. A payment already categorized or split keeps what it has.
    - The schedule moves past the occurrence when this is the payment now
      due. An older payment linked retroactively (a rule applied to history)
      is only linked, so it can't push the schedule into the future.
    - A transaction already linked to any recurring item is left alone.
    """
    if (
        transaction.is_ignored
        or transaction.source in _NOT_REAL
        or transaction.recurring_transaction_id is not None
        or transaction.type != recurring.type
        or transaction.workspace_id != recurring.workspace_id
    ):
        return False

    placeholder = await _placeholder_for(session, recurring, transaction.date)
    if placeholder is not None and placeholder.id != transaction.id:
        # A split payment keeps category_id empty: that is how a split row
        # is told apart (category_line_service's invariant).
        if transaction.category_id is None and not transaction.has_category_lines:
            transaction.category_id = placeholder.category_id
        await session.delete(placeholder)

    transaction.recurring_transaction_id = recurring.id
    await category_line_service.apply_template_split(session, transaction, recurring)
    if recurring.is_active and transaction.date >= recurring.next_occurrence - half_period(
        recurring.frequency
    ):
        recurring_match_service.advance_past(recurring, transaction.date)
    await session.flush()
    return True


def previous_occurrence(recurring: RecurringTransaction) -> Optional[date]:
    """The occurrence before `next_occurrence` on the item's schedule, or None
    when the next one is the first."""
    from app.services.recurring_transaction_service import _advance_date

    intended_day = recurring.day_of_month or recurring.start_date.day
    previous: Optional[date] = None
    current = recurring.start_date
    guard = 0
    while current < recurring.next_occurrence and guard < 5000:
        previous = current
        current = _advance_date(current, recurring.frequency, intended_day=intended_day)
        guard += 1
    return previous


async def _covered(
    session: AsyncSession,
    recurring: RecurringTransaction,
    occurrence: date,
    except_id: Optional[uuid.UUID] = None,
) -> bool:
    """Whether a transaction linked to the item already stands for `occurrence`
    (a payment, or the placeholder generated for it)."""
    half = half_period(recurring.frequency)
    statement = select(Transaction.id).where(
        Transaction.recurring_transaction_id == recurring.id,
        Transaction.is_ignored == False,  # noqa: E712
        Transaction.date >= occurrence - half,
        Transaction.date <= occurrence + half,
    )
    if except_id is not None:
        statement = statement.where(Transaction.id != except_id)
    return (await session.execute(statement.limit(1))).first() is not None


async def check_next_occurrence(
    session: AsyncSession, recurring: RecurringTransaction, new_next: date
) -> None:
    """Validate a next date typed by the user. Raises ValueError.

    It may go back one period at most (to undo a link that moved the item by
    mistake), and not to an occurrence a linked payment already covers, so a
    month is never generated twice. Later dates are always allowed.
    """
    if new_next >= recurring.next_occurrence:
        return
    floor = previous_occurrence(recurring) or recurring.start_date
    if new_next < floor:
        raise ValueError(
            f"The next date can go back one period at most, to {floor.isoformat()}"
        )
    if await _covered(session, recurring, new_next):
        raise ValueError("A payment linked to this item already covers that date")


async def unlink_and_step_back(
    session: AsyncSession, workspace_id: uuid.UUID, transaction: Transaction
) -> None:
    """Clear `transaction`'s recurring link, stepping the item back when this
    payment is what moved it on (see `step_back_after_unlink`). Does not commit.

    Called by upstream's transaction_service.unlink_recurring_transaction in
    place of clearing the foreign key itself; upstream's docstring there says
    the bill is left as-is, which the fork changed on purpose (HISTORY.md #26).
    """
    recurring = await session.get(RecurringTransaction, transaction.recurring_transaction_id)
    transaction.recurring_transaction_id = None
    if recurring is not None and recurring.workspace_id == workspace_id:
        await step_back_after_unlink(session, recurring, transaction)


async def step_back_after_unlink(
    session: AsyncSession, recurring: RecurringTransaction, transaction: Transaction
) -> bool:
    """Undo the move a link made, after `transaction` was unlinked. Does not commit.

    Linking a payment moves the item past the occurrence it stands for. When
    that payment is unlinked, the item goes back to that occurrence if:
    the payment is dated within one period before it (so it is the one that
    moved the item, not an old payment linked to history), no other linked
    payment covers it, and it is not an old month (no earlier than half a
    period before today).
    """
    if not recurring.is_active:
        return False
    previous = previous_occurrence(recurring)
    if previous is None:
        return False
    half = half_period(recurring.frequency)
    if previous < app_today() - half:
        return False
    if not (previous - 2 * half <= transaction.date < recurring.next_occurrence):
        return False
    if await _covered(session, recurring, previous, except_id=transaction.id):
        return False
    recurring.next_occurrence = previous
    return True


async def link_by_rule(
    session: AsyncSession, transaction: Transaction, recurring_id: uuid.UUID
) -> bool:
    """Carry out a rule's "link to recurring" action. Does not commit."""
    recurring = await session.get(RecurringTransaction, recurring_id)
    if recurring is None:
        return False
    linked = await link(session, transaction, recurring)
    if linked:
        await refresh_amount(session, recurring)
    return linked


async def forget_recurring(
    session: AsyncSession, workspace_id: uuid.UUID, recurring_id: uuid.UUID
) -> None:
    """Drop what points at a recurring item about to be deleted. Does not commit.

    Rules lose their "link to recurring" action for it, so they stay editable
    (saving a rule that names a missing item is refused). A rule left with no
    action at all is turned off rather than deleted, so its conditions aren't
    lost. The amount-mode setting goes too.
    """
    target = str(recurring_id)
    rules = (
        (await session.execute(select(Rule).where(Rule.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    for rule in rules:
        actions = list(rule.actions or [])
        kept = [
            a
            for a in actions
            if not (
                isinstance(a, dict)
                and a.get("op") == LINK_RECURRING_ACTION
                and str(a.get("value")) == target
            )
        ]
        if len(kept) == len(actions):
            continue
        rule.actions = kept  # a new list, so the JSON column is marked dirty
        if not kept:
            rule.is_active = False
    row = await session.get(AppSetting, setting_key(recurring_id))
    if row is not None:
        await session.delete(row)


async def recurring_type(session: AsyncSession, recurring_id: Optional[uuid.UUID]) -> Optional[str]:
    """The direction of a recurring item, for previews (None if unknown)."""
    if recurring_id is None:
        return None
    recurring = await session.get(RecurringTransaction, recurring_id)
    return recurring.type if recurring is not None else None


# ---------------------------------------------------------------------------
# Make recurring from a transaction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LinkRuleSpec:
    """The condition of the rule created alongside a new recurring item."""

    field: Literal["description", "payee", "notes"]
    op: Literal["contains", "equals", "starts_with"]
    value: str
    name: Optional[str] = None  # the rule's name; default "Recurring: <description>"


def _next_after(start: date, frequency: str, day_of_month: Optional[int]) -> date:
    from app.services.recurring_transaction_service import _advance_date

    return _advance_date(start, frequency, intended_day=day_of_month or start.day)


async def _link_history(
    session: AsyncSession, workspace_id: uuid.UUID, rule: Rule, recurring: RecurringTransaction
) -> int:
    """Link the earlier payments the new rule matches, so the average has data."""
    from app.services.rule_engine import evaluate_conditions

    result = await session.execute(
        select(Transaction)
        .where(
            Transaction.workspace_id == workspace_id,
            Transaction.recurring_transaction_id.is_(None),
            Transaction.source.not_in(_NOT_REAL),
            Transaction.type == recurring.type,
        )
        .order_by(Transaction.date.asc())
    )
    count = 0
    for tx in result.scalars().all():
        if evaluate_conditions(rule.conditions_op, rule.conditions, tx) and await link(
            session, tx, recurring
        ):
            count += 1
    return count


async def _copy_split(
    session: AsyncSession, recurring: RecurringTransaction, transaction: Transaction
) -> None:
    """Give a recurring item made from a split payment the same split.

    Without it the item would have no category at all (a split row's own
    category is empty). If the split no longer validates (a part's category
    was hidden since), the item takes the largest part's category instead.
    """
    from app.schemas.transaction_category_line import TransactionCategoryLinesInput

    lines = sorted(transaction.category_lines, key=lambda line: line.position)
    if len(lines) < 2:
        return
    share_type = lines[0].share_type
    payload = TransactionCategoryLinesInput.model_validate(
        {
            "share_type": share_type,
            "lines": [
                {"category_id": line.category_id, "notes": line.notes}
                | ({"share_pct": line.share_pct} if share_type == "percent" else {"amount": line.amount})
                for line in lines
            ],
        }
    )
    try:
        async with session.begin_nested():
            await category_line_service.replace_template_lines(session, recurring, payload)
    except ValueError:
        await session.refresh(recurring)
        recurring.category_id = max(lines, key=lambda line: line.amount).category_id


def _unique_rule_name(base: str, taken: set[str]) -> str:
    name = base[:255]
    n = 2
    while name in taken:
        suffix = f" ({n})"
        name = base[: 255 - len(suffix)] + suffix
        n += 1
    return name


async def create_from_transaction(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID,
    *,
    frequency: Frequency,
    description: Optional[str] = None,
    day_of_month: Optional[int] = None,
    category_id: Optional[uuid.UUID] = None,
    follow_average: bool = True,
    link_rule: Optional[LinkRuleSpec] = None,
) -> tuple[RecurringTransaction, int]:
    """Turn an existing transaction into the first payment of a new recurring item.

    Returns the item and how many earlier payments the new rule linked.
    Commits.
    """
    from app.services.recurring_transaction_service import _verify_account_in_workspace

    transaction = await session.get(Transaction, transaction_id)
    if transaction is None or transaction.workspace_id != workspace_id:
        raise RecurringLinkNotFound("Transaction not found")
    if transaction.source in _NOT_REAL:
        raise RecurringLinkError("This transaction can't become a recurring item")
    if transaction.recurring_transaction_id is not None:
        raise RecurringLinkError("This transaction is already linked to a recurring item")
    if transaction.transfer_pair_id is not None:
        raise RecurringLinkError("Transfers between your accounts can't become a recurring item")
    await _verify_account_in_workspace(session, workspace_id, transaction.account_id)

    name = (description or transaction.payee or transaction.description or "").strip()[:500]
    if not name:
        raise RecurringLinkError("A description is required")
    if link_rule is not None and not link_rule.value.strip():
        raise RecurringLinkError("The rule needs a value to match")

    if category_id is not None:
        from app.services.category_service import get_assignable_category_ids

        if category_id not in await get_assignable_category_ids(session, workspace_id):
            raise RecurringLinkError("Category not found")

    monthly_like = frequency in ("monthly", "quarterly", "semiannual", "yearly")
    day = (day_of_month or transaction.date.day) if monthly_like else None
    account = await session.get(Account, transaction.account_id)

    recurring = RecurringTransaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=transaction.account_id,
        category_id=category_id or transaction.category_id,
        description=name,
        amount=Decimal(str(transaction.amount)),
        currency=transaction.currency,
        type=transaction.type,
        frequency=frequency,
        weekend_adjustment="none",
        day_of_month=day,
        start_date=transaction.date,
        # A bank-synced account brings the real payment by itself, so a
        # generated placeholder would only be noise to match away. A manual
        # account has nothing else that writes the payment.
        auto_generate=account is None or account.connection_id is None,
        next_occurrence=_next_after(transaction.date, frequency, day),
    )
    session.add(recurring)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise RecurringLinkError(
            "A recurring item with this description already starts on this date"
        )

    transaction.recurring_transaction_id = recurring.id
    if category_id is None and transaction.has_category_lines:
        await _copy_split(session, recurring, transaction)
    await stamp_primary_amount(session, user_id, recurring, date_field="start_date")

    linked_history = 0
    if link_rule is not None:
        taken = set(
            (
                await session.execute(select(Rule.name).where(Rule.workspace_id == workspace_id))
            ).scalars()
        )
        rule = Rule(
            user_id=user_id,
            workspace_id=workspace_id,
            name=_unique_rule_name(
                (link_rule.name or "").strip() or f"Recurring: {name}", taken
            ),
            conditions_op="and",
            conditions=[
                {"field": link_rule.field, "op": link_rule.op, "value": link_rule.value.strip()}
            ],
            actions=[{"op": "link_recurring", "value": str(recurring.id)}],
            priority=0,
            is_active=True,
        )
        session.add(rule)
        await session.flush()
        linked_history = await _link_history(session, workspace_id, rule, recurring)

    if follow_average:
        mode = AmountMode()
        session.add(AppSetting(key=setting_key(recurring.id), value=mode.to_json()))
        await refresh_amount(session, recurring, mode)

    await session.commit()
    await session.refresh(recurring)
    return recurring, linked_history
