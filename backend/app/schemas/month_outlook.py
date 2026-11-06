import uuid
from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel


class OutlookEventRead(BaseModel):
    date: date
    kind: Literal["income", "bill", "card_bill", "pending"]
    description: str
    amount: Decimal  # signed: + comes in, - goes out
    account_id: Optional[uuid.UUID] = None
    account_name: Optional[str] = None


class CardBillRead(BaseModel):
    account_id: uuid.UUID
    account_name: str
    due_date: date
    amount: Decimal  # still to pay
    total: Decimal
    paid: Decimal
    source: Literal["bank", "computed"]


class CashAccountRead(BaseModel):
    account_id: uuid.UUID
    account_name: str
    balance: Decimal


class MonthOutlookRead(BaseModel):
    month: date
    currency: str
    today: date
    cash_now: Decimal
    start_balance: Decimal
    income_expected: Decimal
    bills_expected: Decimal
    card_bills: Decimal
    end_balance: Decimal
    lowest_balance: Decimal
    lowest_date: date
    committed_later: Decimal
    events: list[OutlookEventRead]
    card_bill_details: list[CardBillRead]
    cash_accounts: list[CashAccountRead]
