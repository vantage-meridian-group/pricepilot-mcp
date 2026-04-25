"""Read-only access to seeded category benchmark data.

Seeding itself (Keepa-coupled) lives in the private PricePilot platform.
This module only exposes the read paths the MCP server needs.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .models import CategoryBenchmark

# Amazon root categories tracked by the platform.
# (category_id, display_name) — IDs are Keepa rootCategory values.
SEED_CATEGORIES: list[tuple[int, str]] = [
    (16310101, "Grocery & Gourmet Food"),
    (3760901, "Health & Beauty"),
    (1055398, "Household"),
    (2619533011, "Pet Supplies"),
]


def get_category_prices(db: Session, category_id: int, min_rows: int = 100) -> list[int]:
    """Return Buy Box prices (cents) for a category, or [] if below min_rows."""
    rows = (
        db.query(CategoryBenchmark.buy_box_price_cents)
        .filter(
            CategoryBenchmark.category_id == category_id,
            CategoryBenchmark.buy_box_price_cents.isnot(None),
        )
        .all()
    )
    prices = [r[0] for r in rows]
    if len(prices) < min_rows:
        return []
    return prices
