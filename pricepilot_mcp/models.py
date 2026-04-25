"""SQLAlchemy models for category benchmark data.

Read-only from the MCP server's perspective — the tables are populated by
the PricePilot platform's benchmark seeder (Keepa-coupled, kept private).
The MCP server only consumes the captured snapshots.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


class CategoryBenchmark(Base):
    __tablename__ = "category_benchmarks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    asin: Mapped[str] = mapped_column(String(20), nullable=False)
    category_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    category_name: Mapped[str] = mapped_column(String(100), nullable=False)
    buy_box_price_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sales_rank: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        Index("ix_catbench_category_captured", "category_id", "captured_at"),
    )


class CategoryTrendCache(Base):
    __tablename__ = "category_trend_cache"

    category_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    category_name: Mapped[str] = mapped_column(String(100), nullable=False)
    trend_direction: Mapped[str] = mapped_column(String(20), nullable=False)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
