import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.recurring_transaction import RecurringTransactionRead


class LinkRuleCondition(BaseModel):
    """The one condition of the rule that links later payments."""

    field: Literal["description", "payee", "notes"] = "description"
    op: Literal["contains", "equals", "starts_with"] = "contains"
    value: str = Field(min_length=1, max_length=500)


class RecurringFromTransaction(BaseModel):
    frequency: Literal["weekly", "biweekly", "monthly", "quarterly", "semiannual", "yearly"] = (
        "monthly"
    )
    description: Optional[str] = Field(default=None, max_length=500)
    day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    category_id: Optional[uuid.UUID] = None
    # Expected amount follows the average of the last three payments.
    follow_average: bool = True
    # Also create a rule so later payments link themselves.
    link_rule: Optional[LinkRuleCondition] = None


class RecurringFromTransactionResult(BaseModel):
    recurring: RecurringTransactionRead
    linked_history: int


class AmountModeRead(BaseModel):
    recurring_id: uuid.UUID
    mode: Literal["average"] = "average"
    window: int


class AmountModeWrite(BaseModel):
    # False keeps the current amount fixed, so it can be edited by hand.
    follow_average: bool
    window: int = Field(default=3, ge=1, le=12)
