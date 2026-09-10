"""Add the versioned P3 market observation and baseline engine schema.

Revision ID: 0003
Revises: 0002

Downgrade is intentionally guarded: P3 revisions and non-listing evidence cannot be
represented by the P0 table and will raise rather than be silently discarded.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "crawl_run",
        sa.Column("data_mode", sa.String(length=16), server_default="sample", nullable=False),
    )
    op.create_index("ix_crawl_run_mode_started", "crawl_run", ["data_mode", "started_at"])
    op.create_check_constraint(
        "crawl_run_data_mode", "crawl_run", "data_mode IN ('demo', 'sample', 'live')"
    )

    op.drop_constraint("uq_market_baseline_slice", "market_baseline", type_="unique")
    op.drop_index("ix_market_baseline_lookup", table_name="market_baseline")
    op.rename_table("market_baseline", "market_baseline_legacy")
    op.execute(
        "ALTER TABLE market_baseline_legacy RENAME CONSTRAINT "
        "pk_market_baseline TO pk_market_baseline_legacy"
    )

    op.create_table(
        "baseline_materialization_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("calculation_version", sa.String(length=40), nullable=False),
        sa.Column("configuration_version", sa.String(length=80), nullable=False),
        sa.Column("input_signature", sa.String(length=64), nullable=False),
        sa.Column("input_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("baseline_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="baseline_materialization_run_data_mode",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="baseline_materialization_run_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_baseline_materialization_run"),
        sa.UniqueConstraint(
            "as_of_date",
            "data_mode",
            "calculation_version",
            "configuration_version",
            "input_signature",
            name="uq_baseline_materialization_run_identity",
        ),
    )
    op.create_index(
        "ix_baseline_materialization_run_started",
        "baseline_materialization_run",
        ["started_at"],
    )
    op.create_index(
        "ix_baseline_materialization_run_latest",
        "baseline_materialization_run",
        ["data_mode", "status", "as_of_date", "finished_at"],
    )

    op.create_table(
        "market_observation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("observation_type", sa.String(length=32), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=80), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=True),
        sa.Column("listing_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("crawl_run_id", sa.Uuid(), nullable=True),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("submarket", sa.String(length=120), nullable=True),
        sa.Column("community", sa.String(length=160), nullable=True),
        sa.Column("area_sqm", sa.Numeric(8, 2), nullable=True),
        sa.Column("area_bucket", sa.String(length=40), nullable=True),
        sa.Column("bedrooms", sa.Integer(), nullable=True),
        sa.Column("layout", sa.String(length=40), nullable=True),
        sa.Column("total_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("monthly_rent", sa.Numeric(14, 2), nullable=True),
        sa.Column("rent_per_sqm", sa.Numeric(14, 2), nullable=True),
        sa.Column("index_value", sa.Numeric(14, 6), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("longitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("latitude", sa.Numeric(10, 7), nullable=True),
        sa.Column("source_confidence", sa.Numeric(5, 4), server_default="1", nullable=False),
        sa.Column("coverage_complete", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("metadata", _jsonb(), server_default="{}", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "area_sqm IS NULL OR area_sqm > 0", name="market_observation_positive_area"
        ),
        sa.CheckConstraint(
            "total_price IS NULL OR total_price >= 0",
            name="market_observation_nonnegative_price",
        ),
        sa.CheckConstraint(
            "unit_price IS NULL OR unit_price >= 0",
            name="market_observation_nonnegative_unit_price",
        ),
        sa.CheckConstraint(
            "monthly_rent IS NULL OR monthly_rent >= 0",
            name="market_observation_nonnegative_rent",
        ),
        sa.CheckConstraint(
            "rent_per_sqm IS NULL OR rent_per_sqm >= 0",
            name="market_observation_nonnegative_rent_per_sqm",
        ),
        sa.CheckConstraint(
            "source_confidence >= 0 AND source_confidence <= 1",
            name="market_observation_confidence_range",
        ),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="market_observation_data_mode",
        ),
        sa.CheckConstraint(
            "observation_type IN "
            "('listing', 'transaction', 'rental', 'official_index', 'external_baseline')",
            name="market_observation_type",
        ),
        sa.CheckConstraint(
            "observation_type NOT IN ('listing', 'transaction') OR "
            "(area_sqm IS NOT NULL AND total_price IS NOT NULL AND unit_price IS NOT NULL)",
            name="market_observation_sale_price_fields",
        ),
        sa.CheckConstraint(
            "observation_type != 'rental' OR "
            "(monthly_rent IS NOT NULL OR rent_per_sqm IS NOT NULL)",
            name="market_observation_rental_fields",
        ),
        sa.CheckConstraint(
            "observation_type != 'official_index' OR index_value IS NOT NULL",
            name="market_observation_index_field",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_market_observation_listing",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["listing_snapshot_id"],
            ["listing_snapshot.id"],
            name="fk_market_observation_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["crawl_run_id"],
            ["crawl_run.id"],
            name="fk_market_observation_crawl_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_observation"),
        sa.UniqueConstraint("listing_snapshot_id", name="uq_market_observation_listing_snapshot"),
        sa.UniqueConstraint(
            "data_mode",
            "observation_type",
            "source",
            "source_record_id",
            "observed_at",
            name="uq_market_observation_source_time",
        ),
    )
    op.create_index("ix_market_observation_listing_id", "market_observation", ["listing_id"])
    op.create_index("ix_market_observation_crawl_run_id", "market_observation", ["crawl_run_id"])
    op.create_index(
        "ix_market_observation_scope_time",
        "market_observation",
        ["observation_type", "district", "submarket", "community", "observed_at"],
    )
    op.create_index(
        "ix_market_observation_listing_time",
        "market_observation",
        ["listing_id", "observed_at"],
    )
    op.create_index(
        "ix_market_observation_mode_type_time",
        "market_observation",
        ["data_mode", "observation_type", "observed_at"],
    )
    op.create_index(
        "ix_market_observation_segment_time",
        "market_observation",
        ["data_mode", "observation_type", "area_bucket", "layout", "observed_at"],
    )

    op.create_table(
        "market_baseline",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("materialization_run_id", sa.Uuid(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("observation_type", sa.String(length=32), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=32), nullable=False),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("submarket", sa.String(length=120), nullable=True),
        sa.Column("community", sa.String(length=160), nullable=True),
        sa.Column("area_bucket", sa.String(length=40), nullable=True),
        sa.Column("layout", sa.String(length=40), nullable=True),
        sa.Column("observation_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("outlier_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("effective_price_sample_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "effective_unit_price_sample_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("price_p10", sa.Numeric(14, 2), nullable=True),
        sa.Column("price_p25", sa.Numeric(14, 2), nullable=True),
        sa.Column("price_p50", sa.Numeric(14, 2), nullable=True),
        sa.Column("price_p75", sa.Numeric(14, 2), nullable=True),
        sa.Column("price_p90", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit_price_p10", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit_price_p25", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit_price_p50", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit_price_p75", sa.Numeric(14, 2), nullable=True),
        sa.Column("unit_price_p90", sa.Numeric(14, 2), nullable=True),
        sa.Column("active_inventory", sa.Integer(), server_default="0", nullable=False),
        sa.Column("new_listings", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_cut_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("price_cut_ratio", sa.Numeric(7, 6), nullable=True),
        sa.Column("median_initial_ask", sa.Numeric(14, 2), nullable=True),
        sa.Column("median_current_ask", sa.Numeric(14, 2), nullable=True),
        sa.Column("median_price_cut_pct", sa.Numeric(7, 6), nullable=True),
        sa.Column("median_days_on_market", sa.Numeric(10, 2), nullable=True),
        sa.Column("relisting_rate", sa.Numeric(7, 6), nullable=True),
        sa.Column("missing_candidate_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("inactive_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ask_price_change_30d", sa.Numeric(9, 6), nullable=True),
        sa.Column("ask_price_change_90d", sa.Numeric(9, 6), nullable=True),
        sa.Column("inventory_change_30d", sa.Numeric(9, 6), nullable=True),
        sa.Column("inventory_change_90d", sa.Numeric(9, 6), nullable=True),
        sa.Column("median_monthly_rent", sa.Numeric(14, 2), nullable=True),
        sa.Column("rent_per_sqm", sa.Numeric(14, 2), nullable=True),
        sa.Column("gross_rental_yield", sa.Numeric(9, 6), nullable=True),
        sa.Column("rental_listing_liquidity", sa.Numeric(7, 4), nullable=True),
        sa.Column("liquidity_score", sa.Numeric(7, 4), nullable=True),
        sa.Column("liquidity_confidence", sa.Numeric(7, 6), nullable=True),
        sa.Column("liquidity_components", _jsonb(), server_default="{}", nullable=False),
        sa.Column("confidence_level", sa.String(length=24), nullable=False),
        sa.Column("confidence_score", sa.Numeric(7, 6), server_default="0", nullable=False),
        sa.Column("confidence_components", _jsonb(), server_default="{}", nullable=False),
        sa.Column("source_types", _jsonb(), server_default="[]", nullable=False),
        sa.Column("sources", _jsonb(), server_default="[]", nullable=False),
        sa.Column("observation_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observation_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("input_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("calculation_version", sa.String(length=40), nullable=False),
        sa.Column("configuration_version", sa.String(length=80), nullable=False),
        sa.Column("baseline_version", sa.String(length=96), nullable=False),
        sa.Column("provenance", _jsonb(), server_default="{}", nullable=False),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')", name="market_baseline_data_mode"
        ),
        sa.CheckConstraint(
            "observation_type IN "
            "('listing', 'transaction', 'rental', 'official_index', 'external_baseline')",
            name="market_baseline_observation_type",
        ),
        sa.CheckConstraint("window_days IN (30, 90, 180, 365)", name="market_baseline_window"),
        sa.CheckConstraint(
            "level IN ('shanghai', 'district', 'submarket', 'community')",
            name="market_baseline_level",
        ),
        sa.CheckConstraint(
            "confidence_level IN ('high', 'medium', 'low', 'insufficient')",
            name="market_baseline_confidence_level",
        ),
        sa.ForeignKeyConstraint(
            ["materialization_run_id"],
            ["baseline_materialization_run.id"],
            name="fk_market_baseline_materialization_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_market_baseline"),
        sa.UniqueConstraint("baseline_version", name="uq_market_baseline_version"),
    )
    op.create_unique_constraint(
        "uq_market_baseline_materialized_slice",
        "market_baseline",
        [
            "materialization_run_id",
            "observation_type",
            "window_days",
            "level",
            "district",
            "submarket",
            "community",
            "area_bucket",
            "layout",
        ],
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_market_baseline_lookup_v2",
        "market_baseline",
        [
            "data_mode",
            "observation_type",
            "window_days",
            "level",
            "district",
            "submarket",
            "as_of_date",
        ],
    )

    _migrate_legacy_baselines()
    op.drop_table("market_baseline_legacy")


def _migrate_legacy_baselines() -> None:
    op.execute(
        """
        INSERT INTO baseline_materialization_run (
            id, as_of_date, data_mode, calculation_version, configuration_version,
            input_signature, input_cutoff_at, status, started_at, finished_at,
            observation_count, baseline_count
        )
        SELECT (
            substr(md5('legacy-run:' || as_of_date::text), 1, 8) || '-' ||
            substr(md5('legacy-run:' || as_of_date::text), 9, 4) || '-' ||
            substr(md5('legacy-run:' || as_of_date::text), 13, 4) || '-' ||
            substr(md5('legacy-run:' || as_of_date::text), 17, 4) || '-' ||
            substr(md5('legacy-run:' || as_of_date::text), 21, 12)
        )::uuid,
            as_of_date, 'sample', 'legacy-v0', 'legacy-v0',
            md5('legacy:' || as_of_date::text), max(computed_at), 'succeeded',
            min(computed_at), max(computed_at), sum(listing_count), count(*)
        FROM market_baseline_legacy
        GROUP BY as_of_date
        """
    )
    op.execute(
        """
        INSERT INTO market_baseline (
            id, materialization_run_id, as_of_date, generated_at, data_mode,
            observation_type, window_days, level, district, submarket, community,
            area_bucket, layout, observation_count, effective_price_sample_count,
            effective_unit_price_sample_count, price_p25, price_p50, price_p75,
            unit_price_p50, active_inventory, new_listings, price_cut_ratio,
            median_current_ask, median_days_on_market, liquidity_score,
            confidence_level, confidence_score, source_types, sources,
            window_start_at, window_end_at, input_cutoff_at, calculation_version,
            configuration_version, baseline_version, provenance
        )
        SELECT legacy.id, run.id, legacy.as_of_date, legacy.computed_at, 'sample',
            'listing', 30, legacy.level, legacy.district, legacy.submarket,
            legacy.community, legacy.area_bucket, legacy.layout, legacy.listing_count,
            legacy.listing_count, legacy.listing_count, legacy.p25, legacy.p50,
            legacy.p75, legacy.median_unit_price, legacy.listing_count,
            legacy.new_listing_30d, legacy.price_drop_ratio_30d, legacy.median_price,
            legacy.median_days_on_market, legacy.liquidity_score, 'insufficient', 0,
            '["listing"]'::jsonb, '[]'::jsonb,
            ((legacy.as_of_date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai') - interval '30 days',
            (legacy.as_of_date + 1)::timestamp AT TIME ZONE 'Asia/Shanghai',
            legacy.computed_at, 'legacy-v0', 'legacy-v0',
            'legacy-v0-' || md5(legacy.id::text),
            jsonb_build_object(
                'legacy_migration', true,
                'assumptions', jsonb_build_array('sample', 'listing', '30_day_window'),
                'asking_price_not_transaction', true
            )
        FROM market_baseline_legacy legacy
        JOIN baseline_materialization_run run
          ON run.as_of_date = legacy.as_of_date
         AND run.calculation_version = 'legacy-v0'
        """
    )


def downgrade() -> None:
    connection = op.get_bind()
    incompatible = connection.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM market_baseline
                WHERE observation_type != 'listing' OR window_days != 30
            ) OR EXISTS (
                SELECT 1 FROM market_baseline
                GROUP BY as_of_date, level, district, submarket, community, area_bucket, layout
                HAVING count(*) > 1
            )
            """
        )
    ).scalar_one()
    if incompatible:
        raise RuntimeError(
            "P3 baseline revisions or non-listing evidence cannot be represented by revision 0002"
        )

    op.create_table(
        "market_baseline_legacy",
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
        sa.PrimaryKeyConstraint("id", name="pk_market_baseline_legacy"),
    )
    op.execute(
        """
        INSERT INTO market_baseline_legacy (
            id, as_of_date, computed_at, level, district, submarket, community,
            area_bucket, layout, p25, p50, p75, median_price, median_unit_price,
            listing_count, new_listing_30d, price_drop_ratio_30d,
            median_days_on_market, liquidity_score
        )
        SELECT id, as_of_date, generated_at, level, district, submarket, community,
            area_bucket, layout, price_p25, price_p50, price_p75, median_current_ask,
            unit_price_p50, observation_count, new_listings, price_cut_ratio,
            median_days_on_market, liquidity_score
        FROM market_baseline
        """
    )

    op.drop_table("market_baseline")
    op.drop_table("market_observation")
    op.drop_table("baseline_materialization_run")
    op.rename_table("market_baseline_legacy", "market_baseline")
    op.execute(
        "ALTER TABLE market_baseline RENAME CONSTRAINT "
        "pk_market_baseline_legacy TO pk_market_baseline"
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
    op.drop_index("ix_crawl_run_mode_started", table_name="crawl_run")
    op.execute("ALTER TABLE crawl_run DROP CONSTRAINT IF EXISTS ck_crawl_run_crawl_run_data_mode")
    op.execute("ALTER TABLE crawl_run DROP CONSTRAINT IF EXISTS crawl_run_data_mode")
    op.drop_column("crawl_run", "data_mode")
