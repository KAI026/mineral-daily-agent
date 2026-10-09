from __future__ import annotations

import json
import math
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from statistics import pstdev
from typing import Any
from urllib.parse import quote

import httpx

from mineral_daily.config import Settings
from mineral_daily.models import (
    PricePoint,
    PriceResult,
    ResultStatus,
    SourceReference,
    TrendResult,
)
from mineral_daily.net import NetworkPolicy, OutboundRequestError, download_limited


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    display_name: str
    currency: str
    unit: str
    is_proxy: bool
    provider: str = "yahoo"
    discovery_note: str | None = None


INSTRUMENTS: dict[str, Instrument] = {
    "copper": Instrument(
        "CPER", "United States Copper Index Fund", "USD", "USD/share", True, "nasdaq"
    ),
    "铜": Instrument(
        "CPER", "United States Copper Index Fund", "USD", "USD/share", True, "nasdaq"
    ),
    "aluminium": Instrument(
        "DBB", "Invesco DB Base Metals Fund", "USD", "USD/share", True, "nasdaq"
    ),
    "aluminum": Instrument(
        "DBB", "Invesco DB Base Metals Fund", "USD", "USD/share", True, "nasdaq"
    ),
    "铝": Instrument(
        "DBB", "Invesco DB Base Metals Fund", "USD", "USD/share", True, "nasdaq"
    ),
    "gold": Instrument("GLD", "SPDR Gold Shares", "USD", "USD/share", True, "nasdaq"),
    "黄金": Instrument("GLD", "SPDR Gold Shares", "USD", "USD/share", True, "nasdaq"),
    "silver": Instrument("SLV", "iShares Silver Trust", "USD", "USD/share", True, "nasdaq"),
    "白银": Instrument("SLV", "iShares Silver Trust", "USD", "USD/share", True, "nasdaq"),
    "lithium": Instrument(
        "LIT", "Global X Lithium & Battery Tech ETF", "USD", "USD/share", True, "nasdaq"
    ),
    "锂": Instrument(
        "LIT", "Global X Lithium & Battery Tech ETF", "USD", "USD/share", True, "nasdaq"
    ),
}


def _instrument_for(commodity: str) -> Instrument | None:
    return INSTRUMENTS.get(commodity.strip().casefold())


def _timestamp(day: date) -> int:
    return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())


def _chart_url(instrument: Instrument, start: date, end: date) -> str:
    if instrument.provider == "nasdaq":
        return (
            f"https://api.nasdaq.com/api/quote/{quote(instrument.symbol, safe='')}/historical"
            "?assetclass=etf&fromdate=2020-01-01&limit=5000"
        )
    symbol = quote(instrument.symbol, safe="")
    return (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        f"?period1={_timestamp(start)}&period2={_timestamp(end)}&interval=1d&events=history"
    )


def parse_chart(payload: dict[str, Any]) -> list[PricePoint]:
    chart = payload.get("chart", {})
    if chart.get("error"):
        raise ValueError(str(chart["error"]))
    results = chart.get("result") or []
    if not results:
        return []
    result = results[0]
    timestamps = result.get("timestamp") or []
    quotes = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quotes.get("close") or []
    points: list[PricePoint] = []
    for stamp, close in zip(timestamps, closes, strict=False):
        if close is None or not math.isfinite(float(close)):
            continue
        points.append(
            PricePoint(
                date=datetime.fromtimestamp(int(stamp), tz=UTC).date(),
                close=round(float(close), 6),
            )
        )
    by_date = {point.date: point for point in points}
    return [by_date[key] for key in sorted(by_date)]


def parse_nasdaq(payload: dict[str, Any]) -> list[PricePoint]:
    status = payload.get("status") or {}
    if status.get("rCode") not in (None, 200):
        raise ValueError(str(status.get("bCodeMessage") or "Nasdaq API error"))
    rows = ((payload.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    points: list[PricePoint] = []
    for row in rows:
        try:
            day = datetime.strptime(str(row["date"]), "%m/%d/%Y").date()
            close = float(str(row["close"]).replace("$", "").replace(",", ""))
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(close):
            points.append(PricePoint(date=day, close=round(close, 6)))
    by_date = {point.date: point for point in points}
    return [by_date[key] for key in sorted(by_date)]


class PriceService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._client = client
        self.network_policy = network_policy or NetworkPolicy()
        self._discovered_instruments: dict[str, Instrument | None] = {}

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://www.nasdaq.com",
                "Referer": "https://www.nasdaq.com/",
            },
        ) as client:
            yield client

    async def _load_points(
        self, instrument: Instrument, start: date, end: date
    ) -> tuple[list[PricePoint], str, str]:
        url = _chart_url(instrument, start, end)
        async with self._http() as client:
            downloaded = await download_limited(
                client,
                url,
                max_bytes=2 * 1024 * 1024,
                accepted_content_types=("application/json", "text/json"),
                policy=self.network_policy,
            )
        payload = json.loads(downloaded.content)
        if instrument.provider == "nasdaq":
            points = parse_nasdaq(payload)
            provider = "Nasdaq historical API"
        else:
            points = parse_chart(payload)
            provider = "Yahoo Finance chart API"
        return points, downloaded.final_url, provider

    async def _resolve_instrument(self, commodity: str) -> Instrument | None:
        configured = _instrument_for(commodity)
        if configured is not None:
            return configured
        search_term = commodity.strip().casefold().replace("_", " ")
        if not search_term or search_term == "unknown":
            return None
        if search_term in self._discovered_instruments:
            return self._discovered_instruments[search_term]

        url = (
            "https://api.nasdaq.com/api/autocomplete/slookup/10?search="
            f"{quote(search_term, safe='')}"
        )
        try:
            async with self._http() as client:
                downloaded = await download_limited(
                    client,
                    url,
                    max_bytes=512 * 1024,
                    accepted_content_types=("application/json", "text/json"),
                    policy=self.network_policy,
                )
            payload = json.loads(downloaded.content)
        except (OutboundRequestError, httpx.HTTPError, ValueError):
            self._discovered_instruments[search_term] = None
            return None

        query_tokens = [token for token in re.findall(r"[a-z0-9]+", search_term) if token]
        rejected_terms = ("ultra", "2x", "3x", "inverse", "short")
        candidates: list[Instrument] = []
        for item in payload.get("data") or []:
            if str(item.get("asset") or "").upper() != "ETF":
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            name = str(item.get("name") or "").strip()
            folded_name = name.casefold()
            if (
                not re.fullmatch(r"[A-Z0-9.-]{1,15}", symbol)
                or not name
                or any(term in folded_name for term in rejected_terms)
                or not all(token in folded_name for token in query_tokens)
            ):
                continue
            candidates.append(
                Instrument(
                    symbol=symbol,
                    display_name=name,
                    currency="USD",
                    unit="USD/share",
                    is_proxy=True,
                    provider="nasdaq",
                    discovery_note=(
                        f"instrument {symbol} was dynamically discovered from Nasdaq autocomplete "
                        f"for commodity '{search_term}'"
                    ),
                )
            )
        selected = candidates[0] if candidates else None
        self._discovered_instruments[search_term] = selected
        return selected

    async def get_price(self, commodity: str, requested_date: str) -> PriceResult:
        instrument = await self._resolve_instrument(commodity)
        try:
            day = date.fromisoformat(requested_date)
        except ValueError:
            return PriceResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                requested_date=date.today(),
                warnings=["date must use ISO format YYYY-MM-DD"],
            )
        if instrument is None:
            return PriceResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                requested_date=day,
                warnings=[f"unsupported commodity: {commodity}"],
            )
        if day > datetime.now(UTC).date():
            return PriceResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                requested_date=day,
                warnings=["future dates are not supported"],
            )
        try:
            points, source_url, provider = await self._load_points(
                instrument, day - timedelta(days=10), day + timedelta(days=1)
            )
        except (OutboundRequestError, httpx.HTTPError, ValueError) as exc:
            return PriceResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                requested_date=day,
                symbol=instrument.symbol,
                instrument=instrument.display_name,
                currency=instrument.currency,
                unit=instrument.unit,
                provider=(
                    "Nasdaq historical API"
                    if instrument.provider == "nasdaq"
                    else "Yahoo Finance chart API"
                ),
                is_proxy=instrument.is_proxy,
                warnings=[str(exc) or type(exc).__name__],
            )
        eligible = [point for point in points if point.date <= day]
        observation = eligible[-1] if eligible else None
        warnings = self._proxy_warnings(instrument)
        if observation is None:
            warnings.append("no observation was available on or before the requested date")
        elif observation.date != day:
            warnings.append(
                f"no observation exists for the requested date; returned the latest prior close "
                f"from {observation.date}"
            )
        return PriceResult(
            status=ResultStatus.OK if observation is not None else ResultStatus.PARTIAL,
            commodity=commodity,
            requested_date=day,
            observation=observation,
            symbol=instrument.symbol,
            instrument=instrument.display_name,
            currency=instrument.currency,
            unit=instrument.unit,
            provider=provider,
            is_proxy=instrument.is_proxy,
            source=SourceReference(
                title=instrument.display_name,
                url=source_url,
                provider=provider,
                retrieved_at=datetime.now(UTC),
            ),
            warnings=warnings,
        )

    async def get_trend(
        self, commodity: str, days: int = 30, end_date: str | None = None
    ) -> TrendResult:
        instrument = await self._resolve_instrument(commodity)
        if not 2 <= days <= 365:
            return TrendResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                days=days,
                warnings=["days must be between 2 and 365"],
            )
        if instrument is None:
            return TrendResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                days=days,
                warnings=[f"unsupported commodity: {commodity}"],
            )
        try:
            end_day = date.fromisoformat(end_date) if end_date else datetime.now(UTC).date()
        except ValueError:
            return TrendResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                days=days,
                warnings=["end_date must use ISO format YYYY-MM-DD"],
            )
        if end_day > datetime.now(UTC).date():
            return TrendResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                days=days,
                warnings=["future end dates are not supported"],
            )
        end = end_day + timedelta(days=1)
        start = end - timedelta(days=days + 12)
        try:
            points, source_url, provider = await self._load_points(instrument, start, end)
        except (OutboundRequestError, httpx.HTTPError, ValueError) as exc:
            return TrendResult(
                status=ResultStatus.ERROR,
                commodity=commodity,
                days=days,
                symbol=instrument.symbol,
                instrument=instrument.display_name,
                currency=instrument.currency,
                unit=instrument.unit,
                provider=(
                    "Nasdaq historical API"
                    if instrument.provider == "nasdaq"
                    else "Yahoo Finance chart API"
                ),
                is_proxy=instrument.is_proxy,
                warnings=[str(exc) or type(exc).__name__],
            )
        points = [point for point in points if point.date <= end_day][-days:]
        warnings = self._proxy_warnings(instrument)
        if len(points) < 2:
            warnings.append("fewer than two observations were available")
            change_absolute = change_percent = None
            direction = "unknown"
        else:
            change_absolute = round(points[-1].close - points[0].close, 6)
            change_percent = (
                round(change_absolute / points[0].close * 100, 4) if points[0].close else None
            )
            direction = "flat"
            if change_absolute > 0:
                direction = "up"
            elif change_absolute < 0:
                direction = "down"
            if len(points) >= 3:
                returns = [
                    (points[i].close / points[i - 1].close) - 1
                    for i in range(1, len(points))
                    if points[i - 1].close
                ]
                warnings.append(
                    f"daily return volatility (population stdev): {pstdev(returns):.4%}"
                )
        status = ResultStatus.OK if len(points) >= 2 else ResultStatus.PARTIAL
        return TrendResult(
            status=status,
            commodity=commodity,
            days=days,
            points=points,
            change_absolute=change_absolute,
            change_percent=change_percent,
            direction=direction,  # type: ignore[arg-type]
            symbol=instrument.symbol,
            instrument=instrument.display_name,
            currency=instrument.currency,
            unit=instrument.unit,
            provider=provider,
            is_proxy=instrument.is_proxy,
            source=SourceReference(
                title=instrument.display_name,
                url=source_url,
                provider=provider,
                retrieved_at=datetime.now(UTC),
            ),
            warnings=warnings,
        )

    @staticmethod
    def _proxy_warnings(instrument: Instrument) -> list[str]:
        if not instrument.is_proxy:
            return []
        warnings = [
            "this is a public market proxy, not an official LME settlement price; the source and "
            "instrument are returned explicitly to prevent benchmark confusion"
        ]
        if instrument.discovery_note:
            warnings.append(instrument.discovery_note)
        return warnings
