from __future__ import annotations

from html import escape

from mineral_daily.models import TrendResult


def build_price_trend_svg(trend: TrendResult) -> str | None:
    """Render a deterministic, dependency-free SVG for a dated price series."""
    if len(trend.points) < 2:
        return None

    width, height = 800, 360
    left, right, top, bottom = 76, 30, 54, 58
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [point.close for point in trend.points]
    low, high = min(values), max(values)
    padding = max((high - low) * 0.08, abs(high) * 0.015, 0.01)
    y_min, y_max = low - padding, high + padding

    first_ordinal = trend.points[0].date.toordinal()
    last_ordinal = trend.points[-1].date.toordinal()
    day_span = max(last_ordinal - first_ordinal, 1)

    def x_position(index: int) -> float:
        ordinal = trend.points[index].date.toordinal()
        return left + (ordinal - first_ordinal) / day_span * plot_width

    def y_position(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_height

    coordinates = [
        (x_position(index), y_position(point.close)) for index, point in enumerate(trend.points)
    ]
    polyline = " ".join(f"{x:.2f},{y:.2f}" for x, y in coordinates)
    instrument = trend.instrument or trend.symbol or trend.commodity
    unit = trend.unit or trend.currency or "价格"
    title = f"{instrument} {len(trend.points)} 个交易日走势"
    description = (
        f"从 {trend.points[0].date} 的 {trend.points[0].close:.4f} 变动至 "
        f"{trend.points[-1].date} 的 {trend.points[-1].close:.4f} {unit}。"
    )

    y_grid: list[str] = []
    for index in range(5):
        value = y_min + (y_max - y_min) * index / 4
        y = y_position(value)
        y_grid.extend(
            [
                f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" class="grid" />',
                f'<text x="{left - 10}" y="{y + 4:.2f}" text-anchor="end" '
                f'class="tick">{value:.2f}</text>',
            ]
        )

    tick_count = min(5, len(trend.points))
    tick_indices = sorted(
        {round(index * (len(trend.points) - 1) / (tick_count - 1)) for index in range(tick_count)}
    )
    x_ticks = [
        f'<text x="{x_position(index):.2f}" y="{height - bottom + 24}" '
        f'text-anchor="middle" class="tick">{trend.points[index].date:%m-%d}</text>'
        for index in tick_indices
    ]

    last_x, last_y = coordinates[-1]
    direction_class = "positive" if (trend.change_percent or 0) >= 0 else "negative"
    change_text = (
        f"区间变化 {trend.change_percent:+.2f}%"
        if trend.change_percent is not None
        else "区间变化不可用"
    )
    proxy_note = "公开市场代理，不是官方 LME 结算价" if trend.is_proxy else "市场价格序列"
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="800" height="360" viewBox="0 0 800 360" role="img" aria-labelledby="chart-title chart-desc">
  <title id="chart-title">{escape(title)}</title>
  <desc id="chart-desc">{escape(description)} {escape(proxy_note)}。</desc>
  <style>
    .background {{ fill: #ffffff; }}
    .frame {{ fill: none; stroke: #94a3b8; stroke-width: 1; }}
    .grid {{ stroke: #e2e8f0; stroke-width: 1; }}
    .series {{ fill: none; stroke: #2563eb; stroke-width: 3; stroke-linejoin: round; stroke-linecap: round; }}
    .point {{ fill: #2563eb; stroke: #ffffff; stroke-width: 2; }}
    .tick {{ fill: #475569; font: 12px system-ui, sans-serif; }}
    .label {{ fill: #0f172a; font: 500 13px system-ui, sans-serif; }}
    .title {{ fill: #0f172a; font: 500 18px system-ui, sans-serif; }}
    .subtitle {{ fill: #475569; font: 12px system-ui, sans-serif; }}
    .positive {{ fill: #047857; font: 500 13px system-ui, sans-serif; }}
    .negative {{ fill: #b91c1c; font: 500 13px system-ui, sans-serif; }}
    @media (prefers-color-scheme: dark) {{
      .background {{ fill: #0f172a; }} .frame {{ stroke: #64748b; }} .grid {{ stroke: #334155; }}
      .tick, .subtitle {{ fill: #cbd5e1; }} .label, .title {{ fill: #f8fafc; }}
      .point {{ stroke: #0f172a; }}
    }}
  </style>
  <rect class="background" width="800" height="360" />
  <text x="{left}" y="26" class="title">{escape(instrument)} 价格走势</text>
  <text x="{left}" y="44" class="subtitle">{len(trend.points)} 个交易日 · {escape(unit)} · {escape(proxy_note)}</text>
  {"".join(y_grid)}
  <rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" class="frame" />
  <polyline points="{polyline}" class="series" />
  <circle cx="{last_x:.2f}" cy="{last_y:.2f}" r="5" class="point" />
  <text x="{min(last_x + 10, width - right - 4):.2f}" y="{max(last_y - 10, top + 14):.2f}" text-anchor="end" class="label">{trend.points[-1].close:.2f}</text>
  {"".join(x_ticks)}
  <text x="{left + plot_width / 2:.2f}" y="{height - 10}" text-anchor="middle" class="tick">日期</text>
  <text x="18" y="{top + plot_height / 2:.2f}" text-anchor="middle" class="tick" transform="rotate(-90 18 {top + plot_height / 2:.2f})">{escape(unit)}</text>
  <text x="{width - right}" y="26" text-anchor="end" class="{direction_class}">{escape(change_text)}</text>
</svg>
'''
