"""The rule action "transfer_to_account": pair a matching transaction with a
counterpart in a manual account, as the "create counterpart" button does.

Kept out of upstream's transaction_service.py, whose create_transfer_counterpart
stays exactly as upstream wrote it. `_add_transfer_counterpart` is this
module's copy of the counterpart it builds, without the commit (a rule runs
inside a bank sync or a rule run). tests/test_rule_transfer_parity.py fails if
the two stop producing the same counterpart, so an upstream change there is
noticed and copied here.
"""

import uuid
from decimal import Decimal
from typing import Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.bank_connection import BankConnection
from app.models.transaction import Transaction
from app.services.credit_card_service import apply_effective_date
from app.services.fx_rate_service import convert as fx_convert
from app.services.fx_rate_service import stamp_primary_amount
from app.services.transaction_service import _ensure_not_mortgage_linked


async def _counterpart_destination(
    session: AsyncSession, workspace_id: uuid.UUID, to_account_id: uuid.UUID
) -> Optional[Account]:
    """The destination account, if it belongs to this workspace."""
    to_result = await session.execute(
        select(Account)
        .outerjoin(BankConnection)
        .where(
            Account.id == to_account_id,
            or_(
                Account.workspace_id == workspace_id,
                BankConnection.workspace_id == workspace_id,
            ),
        )
    )
    return to_result.scalar_one_or_none()


async def _add_transfer_counterpart(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    anchor: Transaction,
    to_account: Account,
) -> Transaction:
    """Create the opposite-side transaction in `to_account` and pair it with
    `anchor`. Flushes but does not commit, so callers inside a larger unit of
    work (a bank sync, a rule run) keep control of the transaction."""
    opposing_type = "credit" if anchor.type == "debit" else "debit"

    # Convert the amount when the destination account uses another currency.
    if anchor.currency != to_account.currency:
        counterpart_amount, _ = await fx_convert(
            session, Decimal(str(anchor.amount)), anchor.currency, to_account.currency, anchor.date
        )
    else:
        counterpart_amount = anchor.amount

    transfer_pair_id = uuid.uuid4()

    counterpart_tx = Transaction(
        user_id=user_id,
        workspace_id=workspace_id,
        account_id=to_account.id,
        description=anchor.description,
        amount=counterpart_amount,
        currency=to_account.currency,
        date=anchor.date,
        type=opposing_type,
        source="transfer",
        notes=anchor.notes,
        transfer_pair_id=transfer_pair_id,
    )
    apply_effective_date(counterpart_tx, to_account)
    session.add(counterpart_tx)

    # Link the anchor into the pair; reports exclude transfer_pair_id.
    anchor.transfer_pair_id = transfer_pair_id

    await session.flush()
    await stamp_primary_amount(session, user_id, counterpart_tx)

    # Cross-currency: keep both sides on the same primary amount.
    if anchor.currency != to_account.currency and anchor.amount_primary is not None:
        counterpart_tx.amount_primary = anchor.amount_primary
        if counterpart_tx.amount and Decimal(str(counterpart_tx.amount)):
            counterpart_tx.fx_rate_used = Decimal(str(anchor.amount_primary)) / Decimal(
                str(counterpart_tx.amount)
            )
    return counterpart_tx


async def transfer_by_rule(
    session: AsyncSession,
    transaction: Transaction,
    to_account_id: uuid.UUID,
) -> bool:
    """Apply a rule's "transfer to account" action to one transaction.

    Creates the counterpart in `to_account_id` the same way the "create
    counterpart" button does, but quietly does nothing when the transaction
    is not eligible, because a rule runs over many rows and one unsuitable
    row must not fail a whole bank sync or rule run:

    - ignored, already paired, or an opening balance / auto-created transfer;
    - already in the destination account;
    - destination missing, closed, or bank-synced (a synced account would
      soon get its own real copy of the movement, so a counterpart there
      would double count);
    - linked to a mortgage payment;
    - a likely duplicate from the bank feed (see `_has_paired_twin`).

    Returns True when a counterpart was created. Flushes, never commits.
    """
    workspace_id = getattr(transaction, "workspace_id", None)
    if workspace_id is None or transaction.id is None:
        return False
    if transaction.is_ignored or transaction.transfer_pair_id is not None:
        return False
    if transaction.source in {"opening_balance", "transfer"}:
        return False
    if transaction.account_id == to_account_id:
        return False

    to_account = await _counterpart_destination(session, workspace_id, to_account_id)
    if to_account is None or to_account.connection_id is not None or to_account.is_closed:
        return False
    try:
        await _ensure_not_mortgage_linked(session, workspace_id, transaction)
    except ValueError:
        return False
    if await _has_paired_twin(session, transaction):
        return False

    await _add_transfer_counterpart(
        session, workspace_id, transaction.user_id, transaction, to_account
    )
    return True


async def _has_paired_twin(session: AsyncSession, transaction: Transaction) -> bool:
    """Whether a synced row looks like the bank reporting a payment twice.

    Some providers send the same payment again under another id. The sync's
    phantom-duplicate cleanup removes the copy that is *not* paired as a
    transfer when a paired twin sits next to it (same account, amount and
    direction, within a day, near-identical description). Pairing the copy
    here would hide it from that cleanup and put the money in the
    destination account twice, so it is left unpaired: the cleanup removes
    it if it was a duplicate, and a real second transfer of the same amount
    on the same day can be paired by hand.
    """
    if transaction.source != "sync":
        return False
    from datetime import timedelta

    from app.services.text_similarity import token_overlap

    siblings = await session.execute(
        select(Transaction).where(
            Transaction.account_id == transaction.account_id,
            Transaction.source == "sync",
            Transaction.amount == transaction.amount,
            Transaction.type == transaction.type,
            Transaction.date >= transaction.date - timedelta(days=1),
            Transaction.date <= transaction.date + timedelta(days=1),
            Transaction.transfer_pair_id.is_not(None),
            Transaction.id != transaction.id,
        )
    )
    text = transaction.original_description or transaction.description
    return any(
        token_overlap(sibling.original_description or sibling.description, text) >= 0.9
        for sibling in siblings.scalars()
    )
