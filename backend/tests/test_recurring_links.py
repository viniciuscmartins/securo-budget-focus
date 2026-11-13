"""Recurring items made from transactions, linked by rules, and averaged.

For bills paid by boleto or Pix whose value changes, and a salary: the
built-in matcher wants an exact amount, so a rule decides the link instead.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.account import Account
from app.models.app_settings import AppSetting
from app.models.recurring_transaction import RecurringTransaction
from app.models.recurring_transaction_category_line import RecurringTransactionCategoryLine
from app.models.rule import Rule
from app.models.transaction import Transaction
from app.services import recurring_link_service as svc
from app.services import rule_service
from app.services.recurring_link_service import AmountMode, LinkRuleSpec, average
from app.services.rule_engine import link_would_apply, recurring_target


@pytest.fixture
async def checking(session, test_user, test_workspace) -> Account:
    acc = Account(
        id=uuid.uuid4(),
        user_id=test_user.id,
        name="Checking",
        type="checking",
        balance=Decimal("0"),
        currency="BRL",
    )
    session.add(acc)
    await session.commit()
    return acc


async def _tx(
    session, user, account, description, amount, day, kind="debit", source="sync", **extra
):
    tx = Transaction(
        id=uuid.uuid4(),
        user_id=user.id,
        account_id=account.id,
        description=description,
        amount=Decimal(amount),
        currency="BRL",
        date=day,
        type=kind,
        source=source,
        status="posted",
        created_at=datetime.now(timezone.utc),
        **extra,
    )
    session.add(tx)
    await session.commit()
    return tx


async def _recurring(
    session,
    user,
    workspace,
    account,
    *,
    amount="1000",
    next_occurrence=date(2026, 11, 10),
    kind="debit",
    frequency="monthly",
) -> RecurringTransaction:
    rec = RecurringTransaction(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        account_id=account.id,
        description="School",
        amount=Decimal(amount),
        currency="BRL",
        type=kind,
        frequency=frequency,
        day_of_month=next_occurrence.day,
        start_date=date(2026, 1, next_occurrence.day),
        next_occurrence=next_occurrence,
        auto_generate=False,
        is_active=True,
    )
    session.add(rec)
    await session.commit()
    return rec


async def _link_rule(session, user, workspace, recurring, text="escola") -> Rule:
    rule = Rule(
        id=uuid.uuid4(),
        user_id=user.id,
        workspace_id=workspace.id,
        name=f"link {text}",
        conditions_op="and",
        conditions=[{"field": "description", "op": "contains", "value": text}],
        actions=[{"op": "link_recurring", "value": str(recurring.id)}],
        priority=0,
        is_active=True,
    )
    session.add(rule)
    await session.commit()
    return rule


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_recurring_target_reads_the_first_valid_action():
    rid = uuid.uuid4()
    assert recurring_target([{"op": "set_category", "value": "x"}]) is None
    assert recurring_target([{"op": "link_recurring", "value": "nope"}]) is None
    assert recurring_target([{"op": "link_recurring", "value": str(rid)}]) == rid


def test_link_would_apply_mirrors_the_row_checks():
    rid = uuid.uuid4()
    base = dict(is_ignored=False, recurring_transaction_id=None, source="sync", type="debit")
    assert link_would_apply(Transaction(**base), rid, "debit")
    assert not link_would_apply(Transaction(**base), None)
    assert not link_would_apply(Transaction(**base), rid, "credit")
    assert not link_would_apply(Transaction(**{**base, "is_ignored": True}), rid)
    assert not link_would_apply(
        Transaction(**{**base, "recurring_transaction_id": uuid.uuid4()}), rid
    )
    assert not link_would_apply(Transaction(**{**base, "source": "recurring"}), rid)


def test_average_rounds_to_cents_and_handles_empty():
    assert average([]) is None
    assert average([Decimal("100"), Decimal("100"), Decimal("101")]) == Decimal("100.33")
    assert average([Decimal("1200.50")]) == Decimal("1200.50")


def test_amount_mode_round_trip_and_bad_json():
    assert AmountMode.from_json(AmountMode(window=3).to_json()) == AmountMode(window=3)
    assert AmountMode.from_json("not json") is None
    assert AmountMode.from_json('{"mode": "median"}') is None
    assert AmountMode.from_json('{"window": 0}') is None


# ---------------------------------------------------------------------------
# the rule action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_links_a_new_payment_and_moves_the_schedule(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    await _link_rule(session, test_user, test_workspace, rec)
    # Paid a few days early, with a different amount and a Pix description.
    tx = await _tx(session, test_user, checking, "PIX ESCOLA ABC LTDA", "1080", date(2026, 11, 7))

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    assert tx.recurring_transaction_id == rec.id
    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 12, 10)


@pytest.mark.asyncio
async def test_rule_replaces_the_generated_placeholder(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    category_id = None
    placeholder = await _tx(
        session,
        test_user,
        checking,
        "School",
        "1000",
        date(2026, 11, 10),
        source="recurring",
        recurring_transaction_id=rec.id,
        category_id=category_id,
    )
    await _link_rule(session, test_user, test_workspace, rec)
    tx = await _tx(session, test_user, checking, "Pix escola", "1050", date(2026, 11, 12))

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    assert tx.recurring_transaction_id == rec.id
    assert await session.get(Transaction, placeholder.id) is None


@pytest.mark.asyncio
async def test_rule_skips_wrong_direction_ignored_and_already_linked(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    other = await _recurring(
        session, test_user, test_workspace, checking, next_occurrence=date(2026, 11, 20)
    )
    await _link_rule(session, test_user, test_workspace, rec)
    refund = await _tx(
        session, test_user, checking, "Estorno escola", "50", date(2026, 11, 8), kind="credit"
    )
    ignored = await _tx(
        session, test_user, checking, "escola", "10", date(2026, 11, 8), is_ignored=True
    )
    taken = await _tx(
        session,
        test_user,
        checking,
        "escola",
        "10",
        date(2026, 11, 8),
        recurring_transaction_id=other.id,
    )

    for tx in (refund, ignored, taken):
        await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    assert refund.recurring_transaction_id is None
    assert ignored.recurring_transaction_id is None
    assert taken.recurring_transaction_id == other.id
    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 11, 10)


@pytest.mark.asyncio
async def test_applying_a_rule_to_history_links_old_payments_without_jumping_ahead(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    for month in (8, 9, 10):
        await _tx(session, test_user, checking, "PIX ESCOLA", "1000", date(2026, month, 10))
    rule = await _link_rule(session, test_user, test_workspace, rec)

    changed = await rule_service.apply_single_rule(session, test_workspace.id, rule)

    assert changed == 3
    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 11, 10)  # the November fee is still due


@pytest.mark.asyncio
async def test_rule_validation_rejects_a_recurring_item_of_another_workspace(
    session, test_user, test_workspace, checking
):
    with pytest.raises(ValueError, match="Recurring item not found"):
        await rule_service.validate_rule_definition(
            session, test_workspace.id, [], [{"op": "link_recurring", "value": str(uuid.uuid4())}]
        )
    rec = await _recurring(session, test_user, test_workspace, checking)
    with pytest.raises(ValueError, match="only one recurring"):
        await rule_service.validate_rule_definition(
            session,
            test_workspace.id,
            [],
            [
                {"op": "link_recurring", "value": str(rec.id)},
                {"op": "link_recurring", "value": str(rec.id)},
            ],
        )


@pytest.mark.asyncio
async def test_preview_counts_rows_the_link_would_change(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    await _tx(session, test_user, checking, "PIX ESCOLA", "1000", date(2026, 10, 10))
    await _tx(
        session, test_user, checking, "Estorno escola", "50", date(2026, 10, 11), kind="credit"
    )

    result = await rule_service.preview_rule(
        session,
        test_workspace.id,
        "and",
        [{"field": "description", "op": "contains", "value": "escola"}],
        [{"op": "link_recurring", "value": str(rec.id)}],
    )
    assert result.matched == 2
    assert result.will_change == 1  # the refund is a credit


# ---------------------------------------------------------------------------
# amount follows the average of the last payments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_average_of_the_last_three_payments(session, test_user, test_workspace, checking):
    rec = await _recurring(session, test_user, test_workspace, checking)
    for month, value in ((7, "500"), (8, "900"), (9, "1000"), (10, "1100")):
        await _tx(
            session,
            test_user,
            checking,
            "escola",
            value,
            date(2026, month, 10),
            recurring_transaction_id=rec.id,
        )
    # Never averaged: a placeholder, an ignored row, another currency.
    await _tx(
        session,
        test_user,
        checking,
        "School",
        "9999",
        date(2026, 10, 11),
        source="recurring",
        recurring_transaction_id=rec.id,
    )
    await _tx(
        session,
        test_user,
        checking,
        "escola",
        "9999",
        date(2026, 10, 12),
        is_ignored=True,
        recurring_transaction_id=rec.id,
    )

    await svc.set_amount_mode(session, test_workspace.id, rec.id, AmountMode())

    assert rec.amount == Decimal("1000.00")  # (900 + 1000 + 1100) / 3
    assert (await svc.amount_modes(session, test_workspace.id)) == {rec.id: AmountMode()}


@pytest.mark.asyncio
async def test_average_resizes_the_parts_of_a_split_recurring_item(
    session, test_user, test_workspace, test_categories, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    rec.category_id = None
    rec.has_category_lines = True
    for position, (category, value) in enumerate(
        ((test_categories[0], "600"), (test_categories[1], "400"))
    ):
        session.add(
            RecurringTransactionCategoryLine(
                workspace_id=test_workspace.id,
                recurring_transaction_id=rec.id,
                category_id=category.id,
                amount=Decimal(value),
                share_type="exact",
                position=position,
            )
        )
    await session.commit()
    await _tx(
        session,
        test_user,
        checking,
        "escola",
        "1500",
        date(2026, 10, 10),
        recurring_transaction_id=rec.id,
    )

    await svc.set_amount_mode(session, test_workspace.id, rec.id, AmountMode())

    assert rec.amount == Decimal("1500.00")
    await session.refresh(rec, ["category_lines"])
    parts = sorted(line.amount for line in rec.category_lines)
    assert parts == [Decimal("600.00"), Decimal("900.00")]


@pytest.mark.asyncio
async def test_turning_the_average_off_keeps_the_amount(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    await _tx(
        session,
        test_user,
        checking,
        "escola",
        "1234",
        date(2026, 10, 10),
        recurring_transaction_id=rec.id,
    )
    await svc.set_amount_mode(session, test_workspace.id, rec.id, AmountMode())
    await svc.set_amount_mode(session, test_workspace.id, rec.id, None)

    assert rec.amount == Decimal("1234.00")
    assert await session.get(AppSetting, svc.setting_key(rec.id)) is None


@pytest.mark.asyncio
async def test_new_payment_linked_by_rule_updates_the_average(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    await svc.set_amount_mode(session, test_workspace.id, rec.id, AmountMode())
    await _link_rule(session, test_user, test_workspace, rec)
    tx = await _tx(session, test_user, checking, "PIX ESCOLA", "1300", date(2026, 11, 9))

    await rule_service.apply_rules_to_transaction(session, test_user.id, tx)
    await session.commit()

    await session.refresh(rec)
    assert rec.amount == Decimal("1300.00")


@pytest.mark.asyncio
async def test_refresh_all_catches_links_made_elsewhere_and_cleans_up(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    await svc.set_amount_mode(session, test_workspace.id, rec.id, AmountMode())
    # Linked by the built-in matcher during a sync, outside this module.
    await _tx(
        session,
        test_user,
        checking,
        "escola",
        "1500",
        date(2026, 11, 10),
        recurring_transaction_id=rec.id,
    )
    gone = uuid.uuid4()
    session.add(AppSetting(key=svc.setting_key(gone), value=AmountMode().to_json()))
    await session.commit()

    assert await svc.refresh_all(session) == 1
    await session.refresh(rec)
    assert rec.amount == Decimal("1500.00")
    assert await session.get(AppSetting, svc.setting_key(gone)) is None


# ---------------------------------------------------------------------------
# make recurring from a transaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_make_recurring_copies_the_transaction_and_links_it(
    session, test_user, test_workspace, checking
):
    tx = await _tx(session, test_user, checking, "PIX ACADEMIA FIT", "150", date(2026, 10, 5))

    rec, linked = await svc.create_from_transaction(
        session,
        test_workspace.id,
        test_user.id,
        tx.id,
        frequency="monthly",
        description="Gym",
        follow_average=False,
    )

    assert (rec.description, rec.amount, rec.type, rec.account_id) == (
        "Gym",
        Decimal("150.00"),
        "debit",
        checking.id,
    )
    assert rec.day_of_month == 5 and rec.start_date == date(2026, 10, 5)
    assert rec.next_occurrence == date(2026, 11, 5)
    assert rec.auto_generate is True  # manual account: nothing else writes the payment
    assert linked == 0
    await session.refresh(tx)
    assert tx.recurring_transaction_id == rec.id


@pytest.mark.asyncio
async def test_make_recurring_with_a_rule_links_history_and_averages(
    session, test_user, test_workspace, checking
):
    await _tx(session, test_user, checking, "PIX ESCOLA ABC", "1000", date(2026, 8, 10))
    await _tx(session, test_user, checking, "PIX ESCOLA ABC", "1100", date(2026, 9, 10))
    await _tx(session, test_user, checking, "Mercado", "1100", date(2026, 9, 11))
    latest = await _tx(session, test_user, checking, "PIX ESCOLA ABC", "1200", date(2026, 10, 10))

    rec, linked = await svc.create_from_transaction(
        session,
        test_workspace.id,
        test_user.id,
        latest.id,
        frequency="monthly",
        link_rule=LinkRuleSpec(field="description", op="contains", value="escola abc"),
    )

    assert linked == 2
    assert rec.amount == Decimal("1100.00")
    assert rec.next_occurrence == date(2026, 11, 10)
    [rule] = (
        (await session.execute(select(Rule).where(Rule.workspace_id == test_workspace.id)))
        .scalars()
        .all()
    )
    assert rule.actions == [{"op": "link_recurring", "value": str(rec.id)}]

    # The next payment links itself through the rule.
    nov = await _tx(session, test_user, checking, "PIX ESCOLA ABC", "1300", date(2026, 11, 9))
    await rule_service.apply_rules_to_transaction(session, test_user.id, nov)
    await session.commit()
    await session.refresh(rec)
    assert nov.recurring_transaction_id == rec.id
    assert rec.amount == Decimal("1200.00")  # (1100 + 1200 + 1300) / 3
    assert rec.next_occurrence == date(2026, 12, 10)


@pytest.mark.asyncio
async def test_make_recurring_from_a_salary_credit(session, test_user, test_workspace, checking):
    pay = await _tx(
        session, test_user, checking, "SALARIO ACME", "8000", date(2026, 10, 5), kind="credit"
    )

    rec, _ = await svc.create_from_transaction(
        session,
        test_workspace.id,
        test_user.id,
        pay.id,
        frequency="monthly",
        description="Salary",
    )
    assert rec.type == "credit" and rec.amount == Decimal("8000.00")


@pytest.mark.asyncio
async def test_make_recurring_refuses_placeholders_linked_and_transfers(
    session, test_user, test_workspace, checking
):
    rec = await _recurring(session, test_user, test_workspace, checking)
    placeholder = await _tx(
        session, test_user, checking, "School", "1000", date(2026, 10, 10), source="recurring"
    )
    linked = await _tx(
        session, test_user, checking, "x", "1", date(2026, 10, 10), recurring_transaction_id=rec.id
    )
    transfer = await _tx(
        session, test_user, checking, "y", "1", date(2026, 10, 10), transfer_pair_id=uuid.uuid4()
    )

    for tx, msg in (
        (placeholder, "can't become"),
        (linked, "already linked"),
        (transfer, "Transfers"),
    ):
        with pytest.raises(svc.RecurringLinkError, match=msg):
            await svc.create_from_transaction(
                session, test_workspace.id, test_user.id, tx.id, frequency="monthly"
            )
    with pytest.raises(svc.RecurringLinkNotFound):
        await svc.create_from_transaction(
            session, test_workspace.id, test_user.id, uuid.uuid4(), frequency="monthly"
        )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_make_recurring_and_amount_mode(
    client, auth_headers, session, test_user, checking
):
    tx = await _tx(session, test_user, checking, "PIX ESCOLA", "1000", date(2026, 10, 10))

    r = await client.post(
        f"/api/recurring-links/from-transaction/{tx.id}",
        headers=auth_headers,
        json={
            "frequency": "monthly",
            "description": "School",
            "link_rule": {"field": "description", "op": "contains", "value": "escola"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    rid = body["recurring"]["id"]
    assert body["recurring"]["description"] == "School"
    assert body["linked_history"] == 0

    r = await client.get("/api/recurring-links/amount-modes", headers=auth_headers)
    assert r.status_code == 200 and r.json() == [
        {"recurring_id": rid, "mode": "average", "window": 3}
    ]

    r = await client.put(
        f"/api/recurring-links/{rid}/amount-mode",
        headers=auth_headers,
        json={"follow_average": False},
    )
    assert r.status_code == 200
    r = await client.get("/api/recurring-links/amount-modes", headers=auth_headers)
    assert r.json() == []

    # The app names the rule in the user's language.
    other = await _tx(session, test_user, checking, "ALUGUEL", "2000", date(2026, 10, 5))
    r = await client.post(
        f"/api/recurring-links/from-transaction/{other.id}",
        headers=auth_headers,
        json={
            "frequency": "monthly",
            "link_rule": {"field": "description", "op": "contains", "value": "aluguel",
                          "name": "Recorrente: Aluguel"},
        },
    )
    assert r.status_code == 201, r.text
    names = {x["name"] for x in (await client.get("/api/rules", headers=auth_headers)).json()}
    assert {"Recurring: School", "Recorrente: Aluguel"} <= names

    # Twice is refused: the transaction is already linked.
    r = await client.post(
        f"/api/recurring-links/from-transaction/{tx.id}",
        headers=auth_headers,
        json={"frequency": "monthly"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_api_unknown_ids_are_404_and_bad_input_422(client, auth_headers):
    r = await client.post(
        f"/api/recurring-links/from-transaction/{uuid.uuid4()}",
        headers=auth_headers,
        json={"frequency": "monthly"},
    )
    assert r.status_code == 404
    r = await client.put(
        f"/api/recurring-links/{uuid.uuid4()}/amount-mode",
        headers=auth_headers,
        json={"follow_average": True},
    )
    assert r.status_code == 404
    r = await client.post(
        f"/api/recurring-links/from-transaction/{uuid.uuid4()}",
        headers=auth_headers,
        json={"frequency": "daily"},
    )
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_deleting_a_recurring_item_unlinks_its_rules_and_setting(
    session, test_user, test_workspace, checking
):
    from app.services.recurring_transaction_service import delete_recurring_transaction

    rec = await _recurring(session, test_user, test_workspace, checking)
    other = await _recurring(
        session, test_user, test_workspace, checking, next_occurrence=date(2026, 11, 20)
    )
    only_link = await _link_rule(session, test_user, test_workspace, rec)
    mixed = await _link_rule(session, test_user, test_workspace, rec, text="pix escola")
    mixed.actions = [
        {"op": "append_notes", "value": "escola"},
        {"op": "link_recurring", "value": str(rec.id)},
    ]
    untouched = await _link_rule(session, test_user, test_workspace, other, text="gym")
    await svc.set_amount_mode(session, test_workspace.id, rec.id, AmountMode())

    assert await delete_recurring_transaction(session, rec.id, test_workspace.id)

    for rule in (only_link, mixed, untouched):
        await session.refresh(rule)
    # Nothing left to do: off, but kept so its conditions aren't lost.
    assert (only_link.actions, only_link.is_active) == ([], False)
    assert (mixed.actions, mixed.is_active) == ([{"op": "append_notes", "value": "escola"}], True)
    assert untouched.actions == [{"op": "link_recurring", "value": str(other.id)}]
    assert untouched.is_active
    assert await session.get(AppSetting, svc.setting_key(rec.id)) is None
    # The rule that kept an action saves again (a missing item is refused).
    await rule_service.validate_rule_definition(
        session, test_workspace.id, mixed.conditions, mixed.actions
    )


# ---------------------------------------------------------------------------
# Category splits (upstream v0.18.0)
# ---------------------------------------------------------------------------


def _split(share_type, *parts):
    from app.schemas.transaction_category_line import TransactionCategoryLinesInput

    key = "share_pct" if share_type == "percent" else "amount"
    return TransactionCategoryLinesInput.model_validate(
        {"share_type": share_type, "lines": [{"category_id": c.id, key: v} for c, v in parts]}
    )


@pytest.mark.asyncio
async def test_linking_keeps_a_split_payment_split(
    session, test_user, test_workspace, checking, test_categories
):
    from app.services import category_line_service

    food, home, _ = test_categories
    rec = await _recurring(session, test_user, test_workspace, checking)
    rec.category_id = home.id
    placeholder = await _tx(
        session, test_user, checking, "School", "1000", date(2026, 11, 10),
        source="recurring", recurring_transaction_id=rec.id, category_id=home.id,
    )
    pay = await _tx(session, test_user, checking, "PIX ESCOLA", "1000", date(2026, 11, 9))
    await category_line_service.replace_lines(
        session, pay, _split("exact", (food, Decimal("400")), (home, Decimal("600")))
    )
    await session.commit()

    assert await svc.link(session, pay, rec)
    await session.commit()
    await session.refresh(pay)

    # The placeholder's category must not land on a split row.
    assert pay.category_id is None and pay.has_category_lines
    assert sorted(line.amount for line in pay.category_lines) == [Decimal("400"), Decimal("600")]
    assert await session.get(Transaction, placeholder.id) is None


@pytest.mark.asyncio
async def test_a_split_recurring_item_gives_its_parts_to_a_linked_payment(
    session, test_user, test_workspace, checking, test_categories
):
    from app.services import category_line_service

    food, home, _ = test_categories
    rec = await _recurring(session, test_user, test_workspace, checking)
    await category_line_service.replace_template_lines(
        session, rec, _split("percent", (home, Decimal("70")), (food, Decimal("30")))
    )
    await session.commit()
    pay = await _tx(session, test_user, checking, "PIX ESCOLA", "1200", date(2026, 11, 10))

    assert await svc.link(session, pay, rec)
    await session.commit()
    await session.refresh(pay)

    assert pay.category_id is None and pay.has_category_lines
    parts = {line.category_id: line.amount for line in pay.category_lines}
    assert parts == {home.id: Decimal("840.00"), food.id: Decimal("360.00")}


@pytest.mark.asyncio
async def test_make_recurring_from_a_split_payment_keeps_the_split(
    session, test_user, test_workspace, checking, test_categories
):
    from app.services import category_line_service

    food, home, _ = test_categories
    tx = await _tx(session, test_user, checking, "CONDOMINIO", "900", date(2026, 10, 5))
    await category_line_service.replace_lines(
        session, tx, _split("percent", (home, Decimal("80")), (food, Decimal("20")))
    )
    await session.commit()

    rec, _ = await svc.create_from_transaction(
        session, test_workspace.id, test_user.id, tx.id, frequency="monthly", follow_average=False
    )
    await session.refresh(rec, ["category_lines"])

    assert rec.category_id is None and rec.has_category_lines
    parts = {line.category_id: (line.share_pct, line.amount) for line in rec.category_lines}
    assert parts == {
        home.id: (Decimal("80.00"), Decimal("720.00")),
        food.id: (Decimal("20.00"), Decimal("180.00")),
    }


# ---------------------------------------------------------------------------
# undoing a wrong link: unlink steps back, the next date can be set back
# ---------------------------------------------------------------------------


@pytest.fixture
def nov_13(monkeypatch):
    monkeypatch.setattr(
        "app.services.recurring_link_service.app_today", lambda: date(2026, 11, 13)
    )


async def _linked_salary(session, user, workspace, account, day=date(2026, 11, 13)):
    """A day-30 salary that a payment on the 13th was linked to by mistake,
    which moved the item from Nov 30 to Dec 30."""
    rec = await _recurring(
        session, user, workspace, account,
        amount="7769", next_occurrence=date(2026, 12, 30), kind="credit",
    )
    tx = await _tx(
        session, user, account, "REMUNERACAO/SALARIO", "10580.40", day,
        kind="credit", recurring_transaction_id=rec.id,
    )
    return rec, tx


@pytest.mark.asyncio
async def test_unlinking_the_payment_that_moved_the_item_moves_it_back(
    session, test_user, test_workspace, checking, nov_13
):
    from app.services import transaction_service

    rec, tx = await _linked_salary(session, test_user, test_workspace, checking)

    await transaction_service.unlink_recurring_transaction(session, tx.id, test_workspace.id)

    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 11, 30)


@pytest.mark.asyncio
async def test_unlinking_keeps_the_date_when_another_payment_covers_that_occurrence(
    session, test_user, test_workspace, checking, nov_13
):
    from app.services import transaction_service

    rec, tx = await _linked_salary(session, test_user, test_workspace, checking)
    await _tx(
        session, test_user, checking, "SALARIO", "7769", date(2026, 11, 28),
        kind="credit", recurring_transaction_id=rec.id,
    )

    await transaction_service.unlink_recurring_transaction(session, tx.id, test_workspace.id)

    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 12, 30)


@pytest.mark.asyncio
async def test_unlinking_an_old_payment_does_not_reopen_its_month(
    session, test_user, test_workspace, checking, nov_13
):
    """An October payment did not move the item to December: no step back."""
    from app.services import transaction_service

    rec, tx = await _linked_salary(
        session, test_user, test_workspace, checking, day=date(2026, 10, 30)
    )

    await transaction_service.unlink_recurring_transaction(session, tx.id, test_workspace.id)

    await session.refresh(rec)
    assert rec.next_occurrence == date(2026, 12, 30)


@pytest.mark.asyncio
async def test_the_next_date_can_be_set_back_one_period(
    session, test_user, test_workspace, checking
):
    from app.schemas.recurring_transaction import RecurringTransactionUpdate
    from app.services import recurring_transaction_service

    rec = await _recurring(
        session, test_user, test_workspace, checking,
        next_occurrence=date(2026, 12, 30), kind="credit",
    )

    updated = await recurring_transaction_service.update_recurring_transaction(
        session, rec.id, test_workspace.id,
        RecurringTransactionUpdate(next_occurrence=date(2026, 11, 30)),
    )

    assert updated is not None
    assert updated.next_occurrence == date(2026, 11, 30)


@pytest.mark.asyncio
async def test_the_next_date_goes_back_one_period_at_most(
    session, test_user, test_workspace, checking
):
    from app.schemas.recurring_transaction import RecurringTransactionUpdate
    from app.services import recurring_transaction_service

    rec = await _recurring(
        session, test_user, test_workspace, checking,
        next_occurrence=date(2026, 12, 30), kind="credit",
    )

    with pytest.raises(ValueError, match="one period"):
        await recurring_transaction_service.update_recurring_transaction(
            session, rec.id, test_workspace.id,
            RecurringTransactionUpdate(next_occurrence=date(2026, 10, 30)),
        )


@pytest.mark.asyncio
async def test_the_next_date_cannot_go_back_to_an_occurrence_already_paid(
    session, test_user, test_workspace, checking
):
    from app.schemas.recurring_transaction import RecurringTransactionUpdate
    from app.services import recurring_transaction_service

    rec = await _recurring(
        session, test_user, test_workspace, checking,
        next_occurrence=date(2026, 12, 30), kind="credit",
    )
    await _tx(
        session, test_user, checking, "SALARIO", "7769", date(2026, 11, 30),
        kind="credit", recurring_transaction_id=rec.id,
    )

    with pytest.raises(ValueError, match="already"):
        await recurring_transaction_service.update_recurring_transaction(
            session, rec.id, test_workspace.id,
            RecurringTransactionUpdate(next_occurrence=date(2026, 11, 30)),
        )


@pytest.mark.asyncio
async def test_api_sets_the_next_date_back_and_rejects_too_far(
    client, auth_headers, session, test_user, test_workspace
):
    acc = (await session.execute(select(Account).where(Account.user_id == test_user.id))).scalars().first()
    if acc is None:
        acc = Account(
            id=uuid.uuid4(), user_id=test_user.id, name="Checking", type="checking",
            balance=Decimal("0"), currency="BRL",
        )
        session.add(acc)
        await session.commit()
    rec = await _recurring(
        session, test_user, test_workspace, acc,
        next_occurrence=date(2026, 12, 30), kind="credit",
    )

    ok = await client.patch(
        f"/api/recurring-transactions/{rec.id}",
        json={"next_occurrence": "2026-11-30"}, headers=auth_headers,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["next_occurrence"] == "2026-11-30"

    bad = await client.patch(
        f"/api/recurring-transactions/{rec.id}",
        json={"next_occurrence": "2026-09-30"}, headers=auth_headers,
    )
    assert bad.status_code == 400
