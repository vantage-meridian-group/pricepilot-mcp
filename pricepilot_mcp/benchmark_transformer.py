"""Derived metric helpers — percentile rank, price index, trend direction.

These transformations sit on top of raw benchmark snapshots so that the
public MCP surface only exposes derived statistics, never raw competitor
prices.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from .config import TREND_THRESHOLD, TREND_WINDOW_OBSERVATIONS


class TrendDirection(str, Enum):
    RISING = "Rising"
    STABLE = "Stable"
    FALLING = "Falling"
    INSUFFICIENT_DATA = "Insufficient Data"


def compute_trend(price_history: list[float]) -> TrendDirection:
    """Compute trend by comparing recent N observations to the prior N."""
    if len(price_history) < 10:
        return TrendDirection.INSUFFICIENT_DATA

    window = TREND_WINDOW_OBSERVATIONS
    recent = price_history[-window:]
    older = (
        price_history[-window * 2:-window]
        if len(price_history) >= window * 2
        else price_history[: len(recent)]
    )

    if not older:
        return TrendDirection.INSUFFICIENT_DATA

    avg_recent = sum(recent) / len(recent)
    avg_older = sum(older) / len(older)

    if avg_older == 0:
        return TrendDirection.INSUFFICIENT_DATA

    change = (avg_recent - avg_older) / avg_older

    if change > TREND_THRESHOLD:
        return TrendDirection.RISING
    elif change < -TREND_THRESHOLD:
        return TrendDirection.FALLING
    else:
        return TrendDirection.STABLE


def compute_percentile_rank(
    user_price_cents: int,
    category_prices_cents: list[int],
) -> Optional[int]:
    """Where does user_price sit in the category distribution? (0-100, or None)."""
    if not category_prices_cents:
        return None

    below = sum(1 for p in category_prices_cents if p < user_price_cents)
    return round((below / len(category_prices_cents)) * 100)


def format_percentile_display(rank: int, sample_size: int) -> str:
    """Format percentile for display with bucketed sample size."""
    bucket = (sample_size // 100) * 100
    bucket_label = f"{bucket}+" if bucket > 0 else str(sample_size)
    return f"{rank}th percentile ({bucket_label} products)"


def format_price_index_label(r_mid: float) -> str:
    """Format r_mid as a Price Index display label."""
    pct = abs(r_mid - 1.0) * 100
    if r_mid > 1.005:
        return f"{r_mid:.2f} ({pct:.0f}% above midpoint)"
    elif r_mid < 0.995:
        return f"{r_mid:.2f} ({pct:.0f}% below midpoint)"
    else:
        return f"{r_mid:.2f} (at midpoint)"
