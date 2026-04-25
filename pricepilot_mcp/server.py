"""PricePilot MCP Server — free CPG pricing intelligence for AI assistants.

Exposes derived pricing statistics (percentile rank, trend, price index)
from weekly Amazon category scans. Never exposes raw Keepa prices.

Run via: python -m pricepilot_mcp
"""

from __future__ import annotations

import os
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse

from .benchmark_data import SEED_CATEGORIES, get_category_prices
from .benchmark_transformer import (
    compute_percentile_rank,
    format_percentile_display,
    format_price_index_label,
)
from .config import PARITY_LOWER_BOUND, PARITY_UPPER_BOUND
from .db import SessionLocal
from .models import CategoryBenchmark, CategoryTrendCache

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CTA = (
    "For a full per-SKU pricing report with actionable recommendations, "
    "visit app.pricepilot.vantagemeridiangroup.com"
)
SERVER_VERSION = "1.0.0"
STALE_THRESHOLD_DAYS = 10

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

# Rate limiting defaults
RATE_LIMIT_RPM = int(os.getenv("MCP_RATE_LIMIT_RPM", "60"))
RATE_LIMIT_RPD = int(os.getenv("MCP_RATE_LIMIT_RPD", "1000"))

# Category lookup: name (lowercased) -> (id, display_name)
_CATEGORY_MAP: dict[str, tuple[int, str]] = {}
for _cid, _cname in SEED_CATEGORIES:
    _CATEGORY_MAP[_cname.lower()] = (_cid, _cname)

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

_rate_minute: dict[str, list[float]] = defaultdict(list)
_rate_day: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(consumer: str = "default") -> str | None:
    now = time.time()
    minute_ago = now - 60
    day_ago = now - 86400

    _rate_minute[consumer] = [t for t in _rate_minute[consumer] if t > minute_ago]
    _rate_day[consumer] = [t for t in _rate_day[consumer] if t > day_ago]

    if len(_rate_minute[consumer]) >= RATE_LIMIT_RPM:
        return (
            f"Rate limit reached ({RATE_LIMIT_RPM} requests/minute). "
            f"For unlimited access to pricing intelligence, {CTA}"
        )
    if len(_rate_day[consumer]) >= RATE_LIMIT_RPD:
        return (
            f"Daily rate limit reached ({RATE_LIMIT_RPD} requests/day). "
            f"For unlimited access, {CTA}"
        )

    _rate_minute[consumer].append(now)
    _rate_day[consumer].append(now)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_category(category: str) -> tuple[int, str] | None:
    return _CATEGORY_MAP.get(category.lower())


def _available_categories_text() -> str:
    return ", ".join(name for _, name in SEED_CATEGORIES)


def _get_last_refreshed(db, category_id: int) -> str:
    row = (
        db.query(CategoryBenchmark.captured_at)
        .filter(CategoryBenchmark.category_id == category_id)
        .order_by(CategoryBenchmark.captured_at.desc())
        .first()
    )
    if row and row[0]:
        return row[0].isoformat()
    return "not yet seeded"


def _get_category_count(db, category_id: int) -> int:
    return (
        db.query(CategoryBenchmark)
        .filter(
            CategoryBenchmark.category_id == category_id,
            CategoryBenchmark.buy_box_price_cents.isnot(None),
        )
        .count()
    )


def _bucket_count(n: int) -> str:
    bucket = (n // 100) * 100
    if bucket > 0:
        return f"{bucket}+ products"
    return f"{n} products"


def _classify_position(r_mid: float) -> str:
    if r_mid < PARITY_LOWER_BOUND:
        return "Value"
    elif r_mid > PARITY_UPPER_BOUND:
        return "Premium"
    return "Parity"


def _get_cached_trend(db, category_id: int) -> dict:
    row = (
        db.query(CategoryTrendCache)
        .filter(CategoryTrendCache.category_id == category_id)
        .first()
    )
    if row:
        return {
            "trend_direction": row.trend_direction,
            "product_count": row.product_count,
            "computed_at": row.computed_at.isoformat() if row.computed_at else None,
        }
    return {
        "trend_direction": "Insufficient Data",
        "product_count": 0,
        "computed_at": None,
    }


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

SERVER_INSTRUCTIONS = (
    "Amazon pricing intelligence for multi-channel CPG brands. "
    "Use when a pricing ops lead or founder at a $2M-$50M brand selling on "
    "Amazon plus retail / DTC / wholesale asks: where does my Amazon price "
    "sit against 100+ category peers? Is the category trending up, stable, "
    "or falling? How do my SKUs stack against the shelf? What's the tier "
    "structure — budget / midmarket / premium? "
    "Covers Grocery, Health & Beauty, Household, and Pet Supplies with "
    "weekly Amazon Buy Box data. "
    "Free alternative to NielsenIQ / SPINS syndicated data."
)

mcp = FastMCP(
    "pricepilot",
    instructions=SERVER_INSTRUCTIONS,
    transport_security=TransportSecuritySettings(
        allowed_hosts=[
            "pricepilot-mcp.onrender.com",
            "*.smithery.ai",
            "*.run.tools",
            "localhost:*",
        ],
    ),
)


SERVER_CARD = {
    "serverInfo": {"name": "pricepilot", "version": SERVER_VERSION},
    "authentication": {"required": False},
    "instructions": SERVER_INSTRUCTIONS,
    "tools": [
        {
            "name": "get_price_position",
            "description": (
                "Percentile-rank a product price against 100+ tracked Amazon competitors "
                "in a CPG category. Returns position (Value/Parity/Premium), Price Index, "
                "and percentile rank."
            ),
        },
        {
            "name": "get_category_trend",
            "description": (
                "Report the 30-day price-trend direction (Rising / Stable / Falling) for "
                "a CPG category on Amazon, with sample size and confidence."
            ),
        },
        {
            "name": "get_category_overview",
            "description": (
                "Return pricing-tier breakdown (budget / midmarket / premium bands) and "
                "median for an Amazon CPG category. Use when sizing up a shelf."
            ),
        },
        {
            "name": "compare_products",
            "description": (
                "Compare multiple product prices against an Amazon CPG category's peers. "
                "Returns per-product percentile rank, position, and distance from median."
            ),
        },
        {
            "name": "list_categories",
            "description": (
                "List Amazon CPG categories with product counts and trend direction. "
                "First call in any pricing-analysis workflow."
            ),
        },
        {
            "name": "server_status",
            "description": "Server health + data-freshness check (degraded if category seed is stale).",
        },
    ],
    "resources": [],
    "prompts": [],
}


@mcp.tool(title="Check Competitive Price Position", annotations=READ_ONLY)
def get_price_position(price: float, category: str) -> dict:
    """Percentile-rank a single product price against tracked Amazon competitors in a CPG category.

    Use when a multi-channel CPG brand asks where their Amazon listing price
    sits against 100+ tracked products — e.g. checking whether a $4.99 granola
    is competitively positioned on Amazon, auditing whether a retail MSRP is
    reasonable against Amazon reality before a buyer meeting, or sanity-checking
    a wholesale-to-retail markup.

    Returns:
        percentile_rank (string, e.g. "72nd percentile"),
        price_index_label (ratio vs. category median),
        position (Value / Parity / Premium),
        category (resolved name),
        last_refreshed (ISO timestamp),
        cta (link to full per-SKU report).

    Args:
        price: Product price in dollars (e.g. 4.99). Must be > 0 and <= 10000.
        category: Exact category name — Grocery & Gourmet Food, Health & Beauty,
            Household, or Pet Supplies. Case-insensitive. Call list_categories
            first to confirm available names.
    """
    rate_err = _check_rate_limit()
    if rate_err:
        return {"error": rate_err}

    if price <= 0 or price > 10000:
        return {"error": "Price must be between $0.01 and $10,000.00"}

    resolved = _resolve_category(category)
    if not resolved:
        return {
            "error": f"Unknown category '{category}'.",
            "available_categories": _available_categories_text(),
            "hint": "Use list_categories to see all available categories.",
        }

    cat_id, cat_name = resolved
    db = SessionLocal()
    try:
        prices = get_category_prices(db, cat_id)
        if not prices:
            count = _get_category_count(db, cat_id)
            return {
                "error": f"Insufficient data for '{cat_name}' ({count} products, need 100+).",
                "cta": CTA,
            }

        price_cents = int(price * 100)
        rank = compute_percentile_rank(price_cents, prices)
        median_cents = statistics.median(prices)
        r_mid = price_cents / median_cents if median_cents > 0 else 1.0

        return {
            "percentile_rank": format_percentile_display(rank, len(prices)),
            "price_index_label": format_price_index_label(r_mid),
            "position": _classify_position(r_mid),
            "category": cat_name,
            "last_refreshed": _get_last_refreshed(db, cat_id),
            "cta": CTA,
        }
    finally:
        db.close()


@mcp.tool(title="See Category Pricing Trend", annotations=READ_ONLY)
def get_category_trend(category: str) -> dict:
    """Report the 30-day Amazon price-trend direction for a CPG category.

    Use when a pricing ops lead asks whether category pricing is rising,
    stable, or falling — e.g. setting retail promo calendar against an Amazon
    backdrop, deciding whether to raise wholesale prices during inflationary
    windows, or catching a price war before it spills into their channel.

    Returns:
        trend_direction (Rising / Stable / Falling / Insufficient Data),
        trend_window ("30 days"),
        confidence (note with product count),
        category (resolved name),
        last_refreshed,
        cta.

    Args:
        category: Exact category name — Grocery & Gourmet Food, Health & Beauty,
            Household, or Pet Supplies. Case-insensitive.
    """
    rate_err = _check_rate_limit()
    if rate_err:
        return {"error": rate_err}

    resolved = _resolve_category(category)
    if not resolved:
        return {
            "error": f"Unknown category '{category}'.",
            "available_categories": _available_categories_text(),
        }

    cat_id, cat_name = resolved
    db = SessionLocal()
    try:
        trend = _get_cached_trend(db, cat_id)
        return {
            "trend_direction": trend["trend_direction"],
            "trend_window": "30 days",
            "confidence": f"Based on {trend['product_count']} products tracked weekly",
            "category": cat_name,
            "last_refreshed": trend["computed_at"] or _get_last_refreshed(db, cat_id),
            "cta": CTA,
        }
    finally:
        db.close()


@mcp.tool(title="Get Category Pricing Overview", annotations=READ_ONLY)
def get_category_overview(category: str) -> dict:
    """Return pricing-tier breakdown and category stats for an Amazon CPG category.

    Use when a brand is sizing up a shelf — e.g. evaluating whether a new SKU
    should enter at budget / midmarket / premium tier, benchmarking their
    retail pricing against Amazon tier structure, or preparing for a retail
    buyer meeting that will ask "what's the typical shelf price here?".

    Returns:
        category (resolved name),
        product_count (bucketed, e.g. "100+ products"),
        price_tiers (dict with budget / midmarket / premium dollar bands,
            rounded to nearest $0.50 for abstraction),
        median_price,
        trend_direction,
        last_refreshed,
        cta.

    Args:
        category: Exact category name — Grocery & Gourmet Food, Health & Beauty,
            Household, or Pet Supplies. Case-insensitive.
    """
    rate_err = _check_rate_limit()
    if rate_err:
        return {"error": rate_err}

    resolved = _resolve_category(category)
    if not resolved:
        return {
            "error": f"Unknown category '{category}'.",
            "available_categories": _available_categories_text(),
        }

    cat_id, cat_name = resolved
    db = SessionLocal()
    try:
        prices = get_category_prices(db, cat_id)
        if not prices:
            count = _get_category_count(db, cat_id)
            return {
                "error": f"Insufficient data for '{cat_name}' ({count} products, need 100+).",
                "cta": CTA,
            }

        sorted_prices = sorted(prices)
        n = len(sorted_prices)
        p25 = sorted_prices[n // 4] / 100
        p50 = sorted_prices[n // 2] / 100
        p75 = sorted_prices[3 * n // 4] / 100

        def round_half(v):
            return round(v * 2) / 2

        p25_r = round_half(p25)
        p75_r = round_half(p75)

        trend = _get_cached_trend(db, cat_id)

        return {
            "category": cat_name,
            "product_count": _bucket_count(len(prices)),
            "price_tiers": {
                "budget": f"Below ${p25_r:.2f}",
                "midmarket": f"${p25_r:.2f} – ${p75_r:.2f}",
                "premium": f"Above ${p75_r:.2f}",
            },
            "median_price": f"${round_half(p50):.2f}",
            "trend_direction": trend["trend_direction"],
            "last_refreshed": _get_last_refreshed(db, cat_id),
            "cta": CTA,
        }
    finally:
        db.close()


@mcp.tool(title="Compare Multiple Products", annotations=READ_ONLY)
def compare_products(products: list[dict], category: str) -> dict:
    """Compare multiple product prices against an Amazon CPG category's peers.

    Use when a multi-channel CPG brand needs to stack-rank their SKUs — e.g.
    identifying which SKUs are underpriced relative to Amazon peers, flagging
    products where the Amazon Buy Box sits materially below the retail MSRP,
    or building a cross-channel price-audit table for an ops review. Replaces
    manual store walks and spreadsheet comparisons.

    Returns:
        comparisons (list, per product: name, price, percentile_rank, position,
            vs_median),
        category,
        category_trend,
        sample_size,
        last_refreshed,
        cta.

    Args:
        products: List of items, each a dict with 'name' (string) and 'price'
            (number in dollars). Minimum 1 item; 3-20 is the useful range.
        category: Exact category name — Grocery & Gourmet Food, Health & Beauty,
            Household, or Pet Supplies. Case-insensitive.
    """
    rate_err = _check_rate_limit()
    if rate_err:
        return {"error": rate_err}

    if not products or len(products) < 1:
        return {"error": "Provide at least one product with 'name' and 'price'."}

    resolved = _resolve_category(category)
    if not resolved:
        return {
            "error": f"Unknown category '{category}'.",
            "available_categories": _available_categories_text(),
        }

    cat_id, cat_name = resolved
    db = SessionLocal()
    try:
        prices = get_category_prices(db, cat_id)
        if not prices:
            return {"error": f"Insufficient data for '{cat_name}'."}

        median_cents = statistics.median(prices)
        median_dollars = median_cents / 100
        trend = _get_cached_trend(db, cat_id)

        comparisons = []
        for p in products:
            name = p.get("name", "Unknown")
            price = p.get("price", 0)
            if not isinstance(price, (int, float)) or price <= 0:
                comparisons.append({"name": name, "error": "Invalid price"})
                continue

            price_cents = int(price * 100)
            rank = compute_percentile_rank(price_cents, prices)
            r_mid = price_cents / median_cents if median_cents > 0 else 1.0
            delta = price - median_dollars

            comparisons.append({
                "name": name,
                "price": f"${price:.2f}",
                "percentile_rank": format_percentile_display(rank, len(prices)),
                "position": _classify_position(r_mid),
                "vs_median": (
                    f"${abs(delta):.2f} {'above' if delta > 0 else 'below'} category median"
                    if abs(delta) >= 0.01 else "At category median"
                ),
            })

        return {
            "comparisons": comparisons,
            "category": cat_name,
            "category_trend": trend["trend_direction"],
            "sample_size": _bucket_count(len(prices)),
            "last_refreshed": _get_last_refreshed(db, cat_id),
            "cta": CTA,
        }
    finally:
        db.close()


@mcp.tool(title="List Available Categories", annotations=READ_ONLY)
def list_categories() -> dict:
    """List Amazon CPG categories with current product counts and trend direction.

    Use as the first call in any pricing-analysis workflow — returns the exact
    category names expected by other tools, plus product count and trend for
    each. Lightweight; safe to call before any category-specific query.

    Returns:
        categories (list of {name, product_count, trend_direction, last_refreshed}),
        note (summary of coverage),
        cta.

    Covers Grocery & Gourmet Food, Health & Beauty, Household, and Pet Supplies.
    """
    rate_err = _check_rate_limit()
    if rate_err:
        return {"error": rate_err}

    db = SessionLocal()
    try:
        categories = []
        for cat_id, cat_name in SEED_CATEGORIES:
            count = _get_category_count(db, cat_id)
            trend = _get_cached_trend(db, cat_id)
            categories.append({
                "name": cat_name,
                "product_count": _bucket_count(count) if count > 0 else "not yet seeded",
                "trend_direction": trend["trend_direction"],
                "last_refreshed": _get_last_refreshed(db, cat_id),
            })

        return {
            "categories": categories,
            "note": (
                f"PricePilot tracks pricing across {len(SEED_CATEGORIES)} Amazon root "
                f"categories, refreshed weekly. Full per-SKU analysis available at "
                f"app.pricepilot.vantagemeridiangroup.com"
            ),
            "cta": CTA,
        }
    finally:
        db.close()


@mcp.tool(title="Check Server Status", annotations=READ_ONLY)
def server_status() -> dict:
    """Report PricePilot server health, data freshness, and degraded-state reason.

    Use to check whether category seeding is current (staleness threshold is
    10 days) before trusting downstream tool output. Returns degraded status
    with reason if data is overdue; healthy otherwise.

    Returns:
        server (name),
        version,
        status (healthy / degraded),
        categories_available,
        data_freshness (ISO timestamp of last seed),
        degraded_reason (null if healthy).
    """
    rate_err = _check_rate_limit()
    if rate_err:
        return {"error": rate_err}

    db = SessionLocal()
    try:
        from sqlalchemy import func

        latest = db.query(func.max(CategoryBenchmark.captured_at)).scalar()
        categories_with_data = (
            db.query(CategoryBenchmark.category_id).distinct().count()
        )

        status = "healthy"
        degraded_reason = None

        if latest is None:
            status = "degraded"
            degraded_reason = "No category data seeded yet"
        else:
            age_days = (datetime.now(timezone.utc) - latest).days
            if age_days > STALE_THRESHOLD_DAYS:
                status = "degraded"
                degraded_reason = f"Category seeding overdue ({age_days} days since last seed)"

        return {
            "server": "pricepilot",
            "version": SERVER_VERSION,
            "status": status,
            "categories_available": categories_with_data,
            "data_freshness": f"Last seeded: {latest.isoformat() if latest else 'never'}",
            "degraded_reason": degraded_reason,
        }
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Custom HTTP routes
# ---------------------------------------------------------------------------


@mcp.custom_route("/.well-known/mcp/server-card.json", methods=["GET"])
async def well_known_server_card(request: Request) -> JSONResponse:
    return JSONResponse(SERVER_CARD)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok", "server": "pricepilot"})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Run the MCP server. Supports stdio and streamable-http transports.

    Usage:
        python -m pricepilot_mcp                    # stdio (Claude Desktop)
        python -m pricepilot_mcp --http             # Streamable HTTP (hosted)
        python -m pricepilot_mcp --http --port 8080
    """
    import argparse

    parser = argparse.ArgumentParser(description="PricePilot MCP Server")
    parser.add_argument("--http", action="store_true", help="Use Streamable HTTP transport (hosted mode)")
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("PORT", "8000")),
        help="Port for hosted transport (default: $PORT or 8000)",
    )
    parser.add_argument(
        "--host", default="0.0.0.0",
        help="Host for hosted transport (default: 0.0.0.0)",
    )
    args = parser.parse_args()

    if args.http:
        mcp.settings.port = args.port
        mcp.settings.host = args.host
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
