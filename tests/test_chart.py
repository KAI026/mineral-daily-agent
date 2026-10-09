from __future__ import annotations

from datetime import date

from mineral_daily.chart import build_price_trend_svg
from mineral_daily.models import PricePoint, ResultStatus, TrendResult


def _trend(points: list[PricePoint]) -> TrendResult:
    return TrendResult(
        status=ResultStatus.OK,
        commodity="lithium",
        days=30,
        points=points,
        change_absolute=4,
        change_percent=10,
        direction="up",
        symbol="LIT",
        instrument="Global X Lithium & Battery Tech ETF",
        currency="USD",
        unit="USD/share",
        provider="test",
        is_proxy=True,
    )


def test_build_price_trend_svg_is_accessible_and_labeled() -> None:
    svg = build_price_trend_svg(
        _trend(
            [
                PricePoint(date=date(2026, 9, 1), close=40),
                PricePoint(date=date(2026, 9, 15), close=42),
                PricePoint(date=date(2026, 10, 1), close=44),
            ]
        )
    )

    assert svg is not None
    assert 'role="img"' in svg
    assert "公开市场代理，不是官方 LME 结算价" in svg
    assert "USD/share" in svg
    assert "区间变化 +10.00%" in svg
    assert "&amp;amp;" not in svg
    assert "<script" not in svg


def test_chart_requires_two_points() -> None:
    assert build_price_trend_svg(_trend([PricePoint(date=date(2026, 10, 1), close=44)])) is None
