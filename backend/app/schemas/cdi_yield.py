from datetime import date
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CdiYieldSettingsWrite(BaseModel):
    enabled: bool = True
    # Percentage of CDI the account earns: 100 = 100% do CDI.
    percent: Decimal = Field(gt=0, le=300)
    mode: Literal["business_days", "calendar_days"] = "business_days"
    start_date: date
    # Income tax withheld from each day's yield, e.g. 22.5; 0 posts it gross.
    tax_percent: Decimal = Field(default=Decimal("0"), ge=0, lt=100)


class CdiYieldRead(BaseModel):
    configured: bool
    enabled: bool = False
    percent: Optional[Decimal] = None
    mode: Optional[Literal["business_days", "calendar_days"]] = None
    start_date: Optional[date] = None
    tax_percent: Decimal = Decimal("0")
    last_posted_date: Optional[date] = None
    total_posted: Decimal = Decimal("0")


class CdiYieldRunResult(BaseModel):
    posted_days: int
    settings: CdiYieldRead
