"""Create the canonical P0/P1 property schema.

Revision ID: 0001
Revises: None
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "listing",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_listing_id", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("district", sa.String(length=80), nullable=False),
        sa.Column("submarket", sa.String(length=120), nullable=False),
        sa.Column("community", sa.String(length=160), nullable=False),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column(
            "coordinates",
            geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("area_sqm", sa.Numeric(8, 2), nullable=False),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("living_rooms", sa.Integer(), nullable=True),
        sa.Column("floor", sa.String(length=80), nullable=True),
        sa.Column("total_floors", sa.Integer(), nullable=True),
        sa.Column("orientation", sa.String(length=80), nullable=True),
        sa.Column("year_built", sa.Integer(), nullable=True),
        sa.Column("elevator", sa.Boolean(), nullable=True),
        sa.Column("building_type", sa.String(length=80), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("missing_since_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consecutive_missing_runs", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("area_sqm > 0", name="positive_area"),
        sa.CheckConstraint("total_price >= 0", name="nonnegative_total_price"),
        sa.PrimaryKeyConstraint("id", name="pk_listing"),
        sa.UniqueConstraint("source", "source_listing_id", name="uq_listing_source_external_id"),
    )
    op.create_index("ix_listing_district", "listing", ["district"])
    op.create_index("ix_listing_submarket", "listing", ["submarket"])
    op.create_index("ix_listing_community", "listing", ["community"])
    op.create_index("ix_listing_status", "listing", ["status"])
    op.create_index("ix_listing_market_active", "listing", ["district", "submarket", "status"])
    op.create_index("ix_listing_price_area", "listing", ["total_price", "area_sqm"])
    op.create_index("idx_listing_coordinates", "listing", ["coordinates"], postgresql_using="gist")

    op.create_table(
        "listing_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_listing_snapshot_listing_id_listing",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_snapshot"),
        sa.UniqueConstraint("listing_id", "snapshot_at", name="uq_snapshot_listing_time"),
    )
    op.create_index(
        "ix_snapshot_listing_time_desc", "listing_snapshot", ["listing_id", "snapshot_at"]
    )

    op.create_table(
        "listing_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_value", sa.Text(), nullable=True),
        sa.Column("current_value", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_listing_event_listing_id_listing",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_listing_event"),
    )
    op.create_index("ix_listing_event_listing_id", "listing_event", ["listing_id"])
    op.create_index("ix_listing_event_occurred", "listing_event", ["event_type", "occurred_at"])

    op.create_table(
        "community",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("district", sa.String(length=80), nullable=False),
        sa.Column("submarket", sa.String(length=120), nullable=False),
        sa.Column("community", sa.String(length=160), nullable=False),
        sa.Column(
            "coordinates",
            geoalchemy2.Geometry("POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("year_built", sa.Integer(), nullable=True),
        sa.Column("households", sa.Integer(), nullable=True),
        sa.Column("building_type", sa.String(length=80), nullable=True),
        sa.Column("metro_distance_m", sa.Integer(), nullable=True),
        sa.Column("nearest_metro", sa.String(length=120), nullable=True),
        sa.Column("active_listing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("median_ask_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("median_unit_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("liquidity_score", sa.Numeric(5, 2), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_community"),
        sa.UniqueConstraint("district", "submarket", "community", name="uq_community_market_name"),
    )
    op.create_index("ix_community_market", "community", ["district", "submarket"])
    op.create_index(
        "idx_community_coordinates", "community", ["coordinates"], postgresql_using="gist"
    )

    op.create_table(
        "market_baseline",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("submarket", sa.String(length=120), nullable=True),
        sa.Column("community", sa.String(length=160), nullable=True),
        sa.Column("area_bucket", sa.String(length=40), nullable=True),
        sa.Column("layout", sa.String(length=40), nullable=True),
        sa.Column("p25", sa.Numeric(14, 2), nullable=True),
        sa.Column("p50", sa.Numeric(14, 2), nullable=True),
        sa.Column("p75", sa.Numeric(14, 2), nullable=True),
        sa.Column("median_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("median_unit_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("listing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_listing_30d", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_drop_ratio_30d", sa.Numeric(6, 5), nullable=True),
        sa.Column("median_days_on_market", sa.Numeric(8, 2), nullable=True),
        sa.Column("liquidity_score", sa.Numeric(5, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_market_baseline"),
    )
    op.create_index(
        "ix_market_baseline_lookup",
        "market_baseline",
        ["level", "district", "submarket", "as_of_date"],
    )
    op.create_unique_constraint(
        "uq_market_baseline_slice",
        "market_baseline",
        [
            "as_of_date",
            "level",
            "district",
            "submarket",
            "community",
            "area_bucket",
            "layout",
        ],
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "inquiry",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("broker", sa.String(length=160), nullable=True),
        sa.Column("conversation_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seller_reason", sa.Text(), nullable=True),
        sa.Column("seller_urgency", sa.String(length=80), nullable=True),
        sa.Column("broker_indicated_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("seller_expected_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("existing_offer", sa.Boolean(), nullable=True),
        sa.Column("existing_offer_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("vacant", sa.Boolean(), nullable=True),
        sa.Column("mortgage_status", sa.String(length=80), nullable=True),
        sa.Column("lease_status", sa.String(length=80), nullable=True),
        sa.Column("hukou_status", sa.String(length=80), nullable=True),
        sa.Column("tax_status", sa.String(length=80), nullable=True),
        sa.Column("raw_message", sa.Text(), nullable=False),
        sa.Column("structured_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_inquiry_listing_id_listing",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_inquiry"),
    )
    op.create_index("ix_inquiry_listing_id", "inquiry", ["listing_id"])

    op.create_table(
        "collection_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("records_seen", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_ingested", sa.Integer(), server_default="0", nullable=False),
        sa.Column("records_failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_collection_run"),
    )
    op.create_index("ix_collection_run_source_started", "collection_run", ["source", "started_at"])

    op.create_table(
        "raw_source_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=True),
        sa.Column("source_listing_id", sa.String(length=255), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalization_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["collection_run.id"],
            name="fk_raw_source_record_run_id_collection_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_raw_source_record_listing_id_listing",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_raw_source_record"),
    )
    op.create_index(
        "ix_raw_record_run_external", "raw_source_record", ["run_id", "source_listing_id"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_historical_update()
        RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'historical records are append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER listing_snapshot_append_only
        BEFORE UPDATE ON listing_snapshot
        FOR EACH ROW EXECUTE FUNCTION reject_historical_update()
        """
    )
    op.execute(
        """
        CREATE TRIGGER raw_source_record_append_only
        BEFORE UPDATE ON raw_source_record
        FOR EACH ROW EXECUTE FUNCTION reject_historical_update()
        """
    )


def downgrade() -> None:
    op.drop_table("raw_source_record")
    op.drop_table("collection_run")
    op.drop_table("inquiry")
    op.drop_table("market_baseline")
    op.drop_table("community")
    op.drop_table("listing_event")
    op.drop_table("listing_snapshot")
    op.drop_table("listing")
    op.execute("DROP FUNCTION reject_historical_update()")
