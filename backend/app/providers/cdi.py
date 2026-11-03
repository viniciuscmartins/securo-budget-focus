"""Daily CDI rates from Banco Central do Brasil (SGS series 12).

Series 12 is the CDI rate in percent per business day: one value for every
business day, none for weekends and holidays. That gap is the business-day
calendar, so it is used as one too.

API: https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados
     ?formato=json&dataInicial=DD/MM/YYYY&dataFinal=DD/MM/YYYY
Each item is {"data": "DD/MM/YYYY", "valor": "0.055131"}. One request covers
at most 10 years, so longer ranges are split.
"""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

logger = logging.getLogger(__name__)

SGS_CDI_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.12/dados"
# A little under the API's 10-year cap, so leap days can't push a chunk over.
_MAX_SPAN = timedelta(days=365 * 9)


class CdiRateProvider(Protocol):
    async def fetch(self, start: date, end: date) -> dict[date, Decimal]:
        """Daily CDI rate as a fraction (0.055131% -> Decimal("0.00055131")),
        keyed by business day, for every business day in [start, end]."""
        ...


def _parse_day(raw: str) -> date:
    """SGS dates are DD/MM/YYYY; ISO is accepted too in case that changes."""
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(raw)


def parse_sgs_items(items: list) -> dict[date, Decimal]:
    """Turn the SGS JSON list into {date: daily rate as a fraction}.

    Malformed items are skipped rather than failing the whole batch.
    """
    rates: dict[date, Decimal] = {}
    for item in items or []:
        try:
            day = _parse_day(str(item["data"]))
            percent = Decimal(str(item["valor"]).replace(",", "."))
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        if percent < 0:
            continue
        rates[day] = percent / Decimal(100)
    return rates


class BcbCdiProvider:
    """Fetches CDI rates from the public Banco Central API (no key needed)."""

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout

    async def fetch(self, start: date, end: date) -> dict[date, Decimal]:
        if end < start:
            return {}
        rates: dict[date, Decimal] = {}
        chunk_start = start
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            while chunk_start <= end:
                chunk_end = min(end, chunk_start + _MAX_SPAN)
                resp = await client.get(
                    SGS_CDI_URL,
                    params={
                        "formato": "json",
                        "dataInicial": chunk_start.strftime("%d/%m/%Y"),
                        "dataFinal": chunk_end.strftime("%d/%m/%Y"),
                    },
                )
                # A range with no published value (e.g. only a weekend) may
                # come back as 404 rather than an empty list; treat both alike.
                if resp.status_code == 404:
                    chunk_start = chunk_end + timedelta(days=1)
                    continue
                resp.raise_for_status()
                rates.update(parse_sgs_items(resp.json()))
                chunk_start = chunk_end + timedelta(days=1)
        return rates


_provider: CdiRateProvider | None = None


def get_cdi_provider() -> CdiRateProvider:
    global _provider
    if _provider is None:
        _provider = BcbCdiProvider()
    return _provider


def set_cdi_provider(provider: CdiRateProvider | None) -> None:
    """Swap the provider (tests)."""
    global _provider
    _provider = provider
