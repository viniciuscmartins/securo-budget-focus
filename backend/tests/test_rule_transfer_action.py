"""The "transfer to account" rule action: a matching transaction gets its
counterpart created in a manual account, as the "create counterpart" button does."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.services import rule_service
from app.services.rule_engine import transfer_target, transfer_would_apply


@pytest.fixture
async def cofrinho(session, test_user) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Cofrinho",
        type="savings",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    return acc


async def _tx(session, user, account, description, amount, kind="debit", **extra) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        account_id=account.id,
        description=description,
        amount=Decimal(amount),
        currency="BRL",
        date=date(2026, 10, 30),
        type=kind,
        source="sync",
        created_at=datetime.now(timezone.utc),
        **extra,
    )
    session.add(tx)
    await session.commit()
    return tx


async def _rule(session, workspace, user, to_account_id, *, text="buffer", extra_actions=(), priority=0):
    rule = Rule(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=f"to {to_account_id}",
        conditions_op="and",
        conditions=[{"field": "description", "op": "contains", "value": text}],
        actions=[*extra_actions, {"op": "transfer_to_account", "value": str(to_account_id)}],
        priority=priority,
        is_active=True,
    )
    session.add(rule)
    await session.commit()
    return rule


async def _counterparts(session, account_id):
    rows = await session.execute(select(Transaction).where(Transaction.account_id == account_id))
    return rows.scalars().all()


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_transfer_target_reads_the_first_valid_action():
    acc = uuid.uuid4()
    assert transfer_target([{"op": "set_category", "value": "x"}]) is None
    assert transfer_target([{"op": "transfer_to_account", "value": "nope"}]) is None
    assert transfer_target([{"op": "transfer_to_account", "value": str(acc)}]) == acc


def test_transfer_would_apply_mirrors_the_row_checks():
    dest = uuid.uuid4()
    base = dict(is_ignored=False, transfer_pair_id=None, source="sync", account_id=uuid.uuid4())
    assert transfer_would_apply(Transaction(**base), dest)
    assert not transfer_would_apply(Transaction(**base), None)
    assert not transfer_would_apply(Transaction(**{**base, "is_ignored": True}), dest)
    assert not transfer_would_apply(Transaction(**{**base, "transfer_pair_id": uuid.uuid4()}), dest)
    assert not transfer_would_apply(Transaction(**{**base, "source": "transfer"}), dest)
    assert not transfer_would_apply(Transaction(**{**base, "account_id": dest}), dest)


# ---------------------------------------------------------------------------
# on sync / import (apply_rules_to_transaction)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_matching_debit_creates_a_credit_in_the_manual_account(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _rule(session, test_workspace, test_user, cofrinho.id)
    tx = await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "4000")

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    [counterpart] = await _counterparts(session, cofrinho.id)
    assert counterpart.type == "credit"
    assert counterpart.amount == Decimal("4000")
    assert counterpart.date == tx.date
    assert counterpart.description == "Dinheiro reservado Buffer"
    assert counterpart.source == "transfer"
    assert tx.transfer_pair_id is not None
    assert counterpart.transfer_pair_id == tx.transfer_pair_id


@pytest.mark.asyncio
async def test_matching_credit_creates_a_debit_in_the_manual_account(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _rule(session, test_workspace, test_user, cofrinho.id)
    tx = await _tx(session, test_user, test_account, "Dinheiro retirado Buffer", "1500", kind="credit")

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    [counterpart] = await _counterparts(session, cofrinho.id)
    assert counterpart.type == "debit"
    assert counterpart.amount == Decimal("1500")


@pytest.mark.asyncio
async def test_non_matching_transaction_is_left_alone(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _rule(session, test_workspace, test_user, cofrinho.id)
    tx = await _tx(session, test_user, test_account, "Mercado", "120")

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    assert await _counterparts(session, cofrinho.id) == []
    assert tx.transfer_pair_id is None


@pytest.mark.asyncio
async def test_ignored_by_any_rule_is_not_transferred(
    session, test_user, test_workspace, test_account, cofrinho
):
    # A separate, later rule ignores it: the transfer must respect that.
    await _rule(session, test_workspace, test_user, cofrinho.id, priority=0)
    ignore = Rule(
        id=uuid.uuid4(),
        user_id=test_user.id,
        workspace_id=test_workspace.id,
        name="ignore",
        conditions_op="and",
        conditions=[{"field": "description", "op": "contains", "value": "buffer"}],
        actions=[{"op": "ignore", "value": None}],
        priority=5,
        is_active=True,
    )
    session.add(ignore)
    await session.commit()
    tx = await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "10")

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    assert tx.is_ignored is True
    assert await _counterparts(session, cofrinho.id) == []


@pytest.mark.asyncio
async def test_already_paired_or_same_account_is_skipped(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _rule(session, test_workspace, test_user, cofrinho.id)
    paired = await _tx(
        session, test_user, test_account, "Dinheiro reservado Buffer", "10", transfer_pair_id=uuid.uuid4()
    )
    inside = await _tx(session, test_user, cofrinho, "Dinheiro reservado Buffer", "10")

    for tx in (paired, inside):
        await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    # Only the row created directly in the cofrinho exists there.
    assert [t.id for t in await _counterparts(session, cofrinho.id)] == [inside.id]


@pytest.mark.asyncio
async def test_closed_destination_is_skipped_at_run_time(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _rule(session, test_workspace, test_user, cofrinho.id)
    cofrinho.is_closed = True
    await session.commit()
    tx = await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "10")

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    assert await _counterparts(session, cofrinho.id) == []


# ---------------------------------------------------------------------------
# apply to existing / re-run all rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_to_existing_backfills_once_and_is_idempotent(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "4000")
    await _tx(session, test_user, test_account, "Dinheiro retirado Buffer", "600", kind="credit")
    await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "7000", is_ignored=True)
    rule = await _rule(session, test_workspace, test_user, cofrinho.id)

    count = await rule_service.apply_single_rule(session, test_workspace.id, rule)
    assert count == 2
    rows = await _counterparts(session, cofrinho.id)
    assert sorted((r.type, r.amount) for r in rows) == [("credit", Decimal("4000")), ("debit", Decimal("600"))]

    # Running again (or re-running all rules) must not duplicate anything:
    # the cofrinho copies match "buffer" too, but they are already paired.
    assert await rule_service.apply_single_rule(session, test_workspace.id, rule) == 0
    await rule_service.apply_all_rules(session, test_workspace.id)
    assert len(await _counterparts(session, cofrinho.id)) == 2


@pytest.mark.asyncio
async def test_preview_counts_rows_that_would_be_transferred(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "4000")
    await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "7000", is_ignored=True)

    preview = await rule_service.preview_rule(
        session,
        test_workspace.id,
        "and",
        [{"field": "description", "op": "contains", "value": "buffer"}],
        [{"op": "transfer_to_account", "value": str(cofrinho.id)}],
    )
    assert preview.matched == 2
    assert preview.will_change == 1
    assert await _counterparts(session, cofrinho.id) == []  # a preview writes nothing


# ---------------------------------------------------------------------------
# validation (API)
# ---------------------------------------------------------------------------


def _rule_body(value):
    return {
        "name": "Cofrinho",
        "conditions_op": "and",
        "conditions": [{"field": "description", "op": "contains", "value": "buffer"}],
        "actions": [{"op": "transfer_to_account", "value": value}],
        "apply_to_existing": False,
    }


@pytest.mark.asyncio
async def test_api_accepts_a_manual_account(client, auth_headers, cofrinho):
    r = await client.post("/api/rules", headers=auth_headers, json=_rule_body(str(cofrinho.id)))
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_api_rejects_synced_unknown_and_closed_accounts(
    client, auth_headers, session, test_account, cofrinho
):
    synced = await client.post("/api/rules", headers=auth_headers, json=_rule_body(str(test_account.id)))
    assert synced.status_code == 400
    assert "manual" in synced.json()["detail"]

    unknown = await client.post("/api/rules", headers=auth_headers, json=_rule_body(str(uuid.uuid4())))
    assert unknown.status_code == 400

    cofrinho.is_closed = True
    await session.commit()
    closed = await client.post("/api/rules", headers=auth_headers, json=_rule_body(str(cofrinho.id)))
    assert closed.status_code == 400


@pytest.mark.asyncio
async def test_api_rejects_two_transfer_actions(client, auth_headers, cofrinho):
    body = _rule_body(str(cofrinho.id))
    body["actions"].append({"op": "transfer_to_account", "value": str(cofrinho.id)})
    r = await client.post("/api/rules", headers=auth_headers, json=body)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_a_duplicate_from_the_bank_feed_is_not_transferred_twice(
    session, test_user, test_workspace, test_account, cofrinho
):
    """The bank reports one payment twice under two ids. Pairing both would
    hide the copy from the sync's phantom-duplicate cleanup and put the money
    in the manual account twice."""
    await _rule(session, test_workspace, test_user, cofrinho.id)
    first = await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "4000",
                      external_id="a1")
    await rule_service.apply_rules_to_transaction(session, test_user.id, first)
    await session.commit()

    twin = await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "4000",
                     external_id="b2")
    await rule_service.apply_rules_to_transaction(session, test_user.id, twin)
    await session.commit()

    assert twin.transfer_pair_id is None
    assert len(await _counterparts(session, cofrinho.id)) == 1


@pytest.mark.asyncio
async def test_a_different_payment_the_same_day_is_still_transferred(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _rule(session, test_workspace, test_user, cofrinho.id)
    for description, amount in (("Dinheiro reservado Buffer", "4000"), ("Buffer extra", "4000"),
                                ("Dinheiro reservado Buffer", "150")):
        tx = await _tx(session, test_user, test_account, description, amount)
        await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
        await session.commit()

    assert len(await _counterparts(session, cofrinho.id)) == 3


@pytest.mark.asyncio
async def test_manual_entries_are_never_treated_as_bank_duplicates(
    session, test_user, test_workspace, test_account, cofrinho
):
    await _rule(session, test_workspace, test_user, cofrinho.id)
    for _ in range(2):
        tx = await _tx(session, test_user, test_account, "Dinheiro reservado Buffer", "4000")
        tx.source = "manual"
        await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
        await session.commit()

    assert len(await _counterparts(session, cofrinho.id)) == 2
