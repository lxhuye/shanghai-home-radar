from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from home_radar_models.base import Base, TimestampMixin

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Inquiry(TimestampMixin, Base):
    __tablename__ = "inquiry"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    listing_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    broker: Mapped[str | None] = mapped_column(String(160))
    conversation_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    seller_reason: Mapped[str | None] = mapped_column(Text)
    seller_urgency: Mapped[str | None] = mapped_column(String(80))
    broker_indicated_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    seller_expected_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    existing_offer: Mapped[bool | None]
    existing_offer_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    vacant: Mapped[bool | None]
    mortgage_status: Mapped[str | None] = mapped_column(String(80))
    lease_status: Mapped[str | None] = mapped_column(String(80))
    hukou_status: Mapped[str | None] = mapped_column(String(80))
    tax_status: Mapped[str | None] = mapped_column(String(80))
    raw_message: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload: Mapped[dict[str, Any]] = mapped_column(JsonType, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
