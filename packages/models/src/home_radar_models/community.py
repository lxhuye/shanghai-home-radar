from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Index, Integer, Numeric, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from home_radar_models.base import Base, TimestampMixin


class Community(TimestampMixin, Base):
    __tablename__ = "community"
    __table_args__ = (
        UniqueConstraint("district", "submarket", "community", name="uq_community_market_name"),
        Index("ix_community_market", "district", "submarket"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    district: Mapped[str] = mapped_column(String(80), nullable=False)
    submarket: Mapped[str] = mapped_column(String(120), nullable=False)
    community: Mapped[str] = mapped_column(String(160), nullable=False)
    coordinates: Mapped[Any | None] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=True)
    )
    year_built: Mapped[int | None] = mapped_column(Integer)
    households: Mapped[int | None] = mapped_column(Integer)
    building_type: Mapped[str | None] = mapped_column(String(80))
    metro_distance_m: Mapped[int | None] = mapped_column(Integer)
    nearest_metro: Mapped[str | None] = mapped_column(String(120))
    active_listing_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    median_ask_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    median_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    liquidity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
