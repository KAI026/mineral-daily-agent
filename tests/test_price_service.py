from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from mineral_daily.config import Settings
from mineral_daily.models import ResultStatus
from mineral_daily.net import NetworkPolicy
from mineral_daily.services.price import INSTRUMENTS, PriceService


def _settings() -> Settings:
    return Settings(
        http_timeout_seconds=5,
        pdf_max_bytes=1_000_000,
        article_max_bytes=1_000_000,
        user_agent="tests",
        log_level="INFO",
    )


def test_supported_commodities_use_reachable_nasdaq_proxies() -> None:
    assert {
        commodity: (INSTRUMENTS[commodity].symbol, INSTRUMENTS[commodity].provider)
        for commodity in ("lithium", "gold", "copper", "silver", "aluminium")
    } == {
        "lithium": ("LIT", "nasdaq"),
        "gold": ("GLD", "nasdaq"),
        "copper": ("CPER", "nasdaq"),
        "silver": ("SLV", "nasdaq"),
        "aluminium": ("DBB", "nasdaq"),
    }


@pytest.mark.asyncio
async def test_trend_and_nearest_prior_close() -> None:
    today = datetime.now(UTC).date()
    dates = [today - timedelta(days=4), today - timedelta(days=2), today - timedelta(days=1)]
    payload = {
        "data": {
            "tradesTable": {
                "rows": [
                    {"date": day.strftime("%m/%d/%Y"), "close": f"${close}"}
                    for day, close in zip(dates, [100.0, 105.0, 110.0], strict=True)
                ]
            }
        },
        "status": {"rCode": 200},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        service = PriceService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        )
        trend = await service.get_trend("lithium", days=30)
        price = await service.get_price("lithium", today.isoformat())

    assert trend.status == ResultStatus.OK
    assert trend.change_percent == 10.0
    assert trend.direction == "up"
    assert trend.is_proxy is True
    assert price.observation is not None
    assert price.observation.date == dates[-1]
    assert any("latest prior close" in warning for warning in price.warnings)


@pytest.mark.asyncio
async def test_unsupported_commodity_is_structured_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": []},
            headers={"content-type": "application/json"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PriceService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        ).get_price("unobtainium", date.today().isoformat())

    assert result.status == ResultStatus.ERROR
    assert "unsupported" in result.warnings[0]


@pytest.mark.asyncio
async def test_unknown_supported_name_discovers_relevant_unleveraged_etf() -> None:
    today = datetime.now(UTC).date()
    payload = {
        "data": {
            "tradesTable": {
                "rows": [
                    {
                        "date": (today - timedelta(days=1)).strftime("%m/%d/%Y"),
                        "close": "$12.50",
                    },
                    {
                        "date": (today - timedelta(days=2)).strftime("%m/%d/%Y"),
                        "close": "$12.00",
                    },
                ]
            }
        },
        "status": {"rCode": 200},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "autocomplete" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "symbol": "NIKL",
                            "name": "Sprott Nickel Miners ETF",
                            "asset": "ETF",
                        }
                    ]
                },
                headers={"content-type": "application/json"},
            )
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PriceService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        ).get_trend("nickel", days=30)

    assert result.status == ResultStatus.OK
    assert result.symbol == "NIKL"
    assert result.instrument == "Sprott Nickel Miners ETF"
    assert any("dynamically discovered" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_trend_respects_end_date() -> None:
    today = datetime.now(UTC).date()
    cutoff = today - timedelta(days=2)
    payload = {
        "data": {
            "tradesTable": {
                "rows": [
                    {"date": day.strftime("%m/%d/%Y"), "close": f"${close}"}
                    for day, close in [
                        (today - timedelta(days=4), 100),
                        (cutoff, 102),
                        (today, 999),
                    ]
                ]
            }
        },
        "status": {"rCode": 200},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await PriceService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        ).get_trend("lithium", days=30, end_date=cutoff.isoformat())

    assert result.points[-1].date == cutoff
    assert result.points[-1].close == 102
