"""The rule action "transfer_to_account" builds its counterpart with its own
copy of upstream's code (rule_transfer_service._add_transfer_counterpart), so
upstream's create_transfer_counterpart can stay exactly as upstream wrote it.

These tests fail when the two stop producing the same counterpart: if upstream
changes how the "create counterpart" button builds one, copy the change into
rule_transfer_service.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.fx_rate import FxRate
from app.models.transaction import Transaction
from app.services import rule_transfer_service, transaction_service
from app.services.fx_rate_service import stamp_primary_amount

DAY = date(2026, 10, 30)

# Everything the counterpart row carries that either path sets.
FIELDS = (
    "user_id",
    "workspace_id",
    "account_id",
    "description",
    "amount",
    "currency",
    "date",
    "effective_date",
    "type",
    "source",
    "notes",
    "amount_primary",
    "fx_rate_used",
)


async def _account(session, user, workspace, **extra) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        balance=Decimal("0"),
        **{"name": "Account", "type": "checking", "currency": "BRL", **extra},
    )
    session.add(acc)
    await session.commit()
    return acc


async def _anchor(session, user, workspace, account, kind) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description="Buffer move",
        amount=Decimal("100.01"),  # converts with rounding (20.002 USD)
        currency=account.currency,
        date=DAY,
        type=kind,
        source="manual",
        notes="note kept on both sides",
        created_at=datetime.now(timezone.utc),
    )
    session.add(tx)
    await session.flush()
    # As the app does on create, so the cross-currency branch has a primary
    # amount to carry over to the counterpart.
    await stamp_primary_amount(session, user.id, tx)
    await session.commit()
    return tx


async def _pair_of(session, anchor: Transaction) -> Transaction:
    await session.refresh(anchor)
    rows = await session.execute(
        select(Transaction).where(
            Transaction.transfer_pair_id == anchor.transfer_pair_id,
            Transaction.id != anchor.id,
        )
    )
    return rows.scalar_one()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["debit", "credit"])
@pytest.mark.parametrize(
    "destination",
    [
        {"name": "Savings", "type": "savings", "currency": "BRL"},
        {"name": "Dollars", "type": "savings", "currency": "USD"},
        {
            "name": "Card",
            "type": "credit_card",
            "currency": "BRL",
            "statement_close_day": 25,
            "payment_due_day": 5,
        },
    ],
    ids=["same-currency", "other-currency", "credit-card"],
)
async def test_rule_counterpart_matches_the_create_counterpart_button(
    session, test_user, test_workspace, kind, destination
):
    session.add(
        FxRate(base_currency="USD", quote_currency="BRL", rate=Decimal("5"), date=DAY, source="test")
    )
    session.add(
        FxRate(base_currency="USD", quote_currency="USD", rate=Decimal("1"), date=DAY, source="test")
    )
    source = await _account(session, test_user, test_workspace)
    dest = await _account(session, test_user, test_workspace, **destination)

    by_button = await _anchor(session, test_user, test_workspace, source, kind)
    by_rule = await _anchor(session, test_user, test_workspace, source, kind)

    await transaction_service.create_transfer_counterpart(
        session, test_workspace.id, test_user.id, by_button.id, dest.id
    )
    await rule_transfer_service._add_transfer_counterpart(
        session, test_workspace.id, test_user.id, by_rule, dest
    )
    await session.commit()

    button_pair = await _pair_of(session, by_button)
    rule_pair = await _pair_of(session, by_rule)

    assert {f: getattr(rule_pair, f) for f in FIELDS} == {
        f: getattr(button_pair, f) for f in FIELDS
    }
    # Each anchor is paired with its own counterpart, under its own pair id.
    assert by_rule.transfer_pair_id == rule_pair.transfer_pair_id
    assert by_rule.transfer_pair_id != by_button.transfer_pair_id
    assert by_rule.amount_primary is not None
    if destination["currency"] != "BRL":
        assert rule_pair.amount != by_rule.amount  # converted, not copied
        assert rule_pair.amount_primary == by_rule.amount_primary
