"""Add the P5 future data foundation and assessment cache.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    _create_employment_center()
    _create_future_project()
    _create_future_factor_observation()
    _create_future_assessment()


def _create_employment_center() -> None:
    op.create_table(
        "employment_center",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column(
            "coordinates",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.Column("current_employment_weight", sa.Numeric(10, 6), nullable=False),
        sa.Column("future_employment_weight", sa.Numeric(10, 6), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(7, 6), nullable=False),
        sa.Column("provenance", _jsonb(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="employment_center_data_mode",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="employment_center_confidence_range",
        ),
        sa.CheckConstraint(
            "current_employment_weight >= 0 AND future_employment_weight >= 0",
            name="employment_center_nonnegative_weights",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employment_center"),
        sa.UniqueConstraint(
            "data_mode",
            "source",
            "source_record_id",
            "effective_from",
            name="uq_employment_center_source_effective",
        ),
    )
    op.create_index(
        "ix_employment_center_mode_effective",
        "employment_center",
        ["data_mode", "effective_from"],
    )
    op.create_index(
        "idx_employment_center_coordinates",
        "employment_center",
        ["coordinates"],
        postgresql_using="gist",
    )


def _create_future_project() -> None:
    op.create_table(
        "future_project",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("project_type", sa.String(length=48), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "coordinates",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("submarket", sa.String(length=120), nullable=True),
        sa.Column("community", sa.String(length=160), nullable=True),
        sa.Column("expected_completion", sa.Date(), nullable=True),
        sa.Column("probability", sa.Numeric(7, 6), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(7, 6), nullable=False),
        sa.Column("provenance", _jsonb(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')", name="future_project_data_mode"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="future_project_confidence_range",
        ),
        sa.CheckConstraint(
            "status IN ('current', 'under_construction', 'approved', 'planned', 'conceptual')",
            name="future_project_status",
        ),
        sa.CheckConstraint(
            "probability IS NULL OR (probability >= 0 AND probability <= 1)",
            name="future_project_probability_range",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_future_project"),
        sa.UniqueConstraint(
            "data_mode",
            "source",
            "source_record_id",
            "effective_from",
            name="uq_future_project_source_effective",
        ),
    )
    op.create_index(
        "ix_future_project_scope",
        "future_project",
        ["data_mode", "project_type", "district", "submarket"],
    )
    op.create_index(
        "idx_future_project_coordinates",
        "future_project",
        ["coordinates"],
        postgresql_using="gist",
    )


def _create_future_factor_observation() -> None:
    op.create_table(
        "future_factor_observation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("factor", sa.String(length=64), nullable=False),
        sa.Column("scope_type", sa.String(length=24), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=True),
        sa.Column("district", sa.String(length=80), nullable=True),
        sa.Column("submarket", sa.String(length=120), nullable=True),
        sa.Column("community", sa.String(length=160), nullable=True),
        sa.Column("current_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("future_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("current_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("future_value", sa.Numeric(18, 6), nullable=True),
        sa.Column("unit", sa.String(length=48), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("source_record_id", sa.String(length=255), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confidence", sa.Numeric(7, 6), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("observation_metadata", _jsonb(), server_default="{}", nullable=False),
        sa.Column("provenance", _jsonb(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="future_factor_observation_data_mode",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="future_factor_observation_confidence_range",
        ),
        sa.CheckConstraint(
            "scope_type IN ('shanghai', 'district', 'submarket', 'community', 'listing')",
            name="future_factor_observation_scope",
        ),
        sa.CheckConstraint(
            "current_score IS NULL OR (current_score >= 0 AND current_score <= 100)",
            name="future_factor_observation_current_score_range",
        ),
        sa.CheckConstraint(
            "future_score IS NULL OR (future_score >= 0 AND future_score <= 100)",
            name="future_factor_observation_future_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_future_factor_observation_listing",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_future_factor_observation"),
        sa.UniqueConstraint(
            "data_mode",
            "factor",
            "source",
            "source_record_id",
            "observed_at",
            name="uq_future_factor_source_time",
        ),
    )
    op.create_index(
        "ix_future_factor_scope_latest",
        "future_factor_observation",
        ["data_mode", "factor", "scope_type", "observed_at"],
    )


def _create_future_assessment() -> None:
    op.create_table(
        "future_assessment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("future_assessment_version", sa.String(length=96), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("future_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("future_score_coverage", sa.Numeric(7, 6), nullable=False),
        sa.Column("obsolescence_risk", sa.Numeric(6, 2), nullable=True),
        sa.Column("obsolescence_coverage", sa.Numeric(7, 6), nullable=False),
        sa.Column("structural_alpha", sa.Numeric(7, 2), nullable=False),
        sa.Column("quality_value_quadrant", sa.String(length=40), nullable=True),
        sa.Column("relative_outlook", sa.String(length=32), nullable=False),
        sa.Column("confidence", sa.String(length=24), nullable=False),
        sa.Column("confidence_score", sa.Numeric(7, 6), nullable=False),
        sa.Column("calibration_state", sa.String(length=32), nullable=False),
        sa.Column("scenarios", _jsonb(), server_default="[]", nullable=False),
        sa.Column("factor_breakdown", _jsonb(), server_default="[]", nullable=False),
        sa.Column("risk_breakdown", _jsonb(), server_default="{}", nullable=False),
        sa.Column("structural_components", _jsonb(), server_default="{}", nullable=False),
        sa.Column("warnings", _jsonb(), server_default="[]", nullable=False),
        sa.Column("missing_inputs", _jsonb(), server_default="[]", nullable=False),
        sa.Column("provenance", _jsonb(), server_default="{}", nullable=False),
        sa.Column("fair_value_anchor", sa.Numeric(14, 2), nullable=False),
        sa.Column("value_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("future_model_version", sa.String(length=40), nullable=False),
        sa.Column("scenario_model_version", sa.String(length=40), nullable=False),
        sa.Column("valuation_version", sa.String(length=96), nullable=False),
        sa.Column("baseline_version", sa.String(length=256), nullable=False),
        sa.Column("configuration_version", sa.String(length=80), nullable=False),
        sa.Column("data_version", sa.String(length=96), nullable=False),
        sa.Column("data_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')", name="future_assessment_data_mode"
        ),
        sa.CheckConstraint(
            "future_score IS NULL OR (future_score >= 0 AND future_score <= 100)",
            name="future_assessment_score_range",
        ),
        sa.CheckConstraint(
            "obsolescence_risk IS NULL OR (obsolescence_risk >= 0 AND obsolescence_risk <= 100)",
            name="future_assessment_obsolescence_range",
        ),
        sa.CheckConstraint(
            "structural_alpha >= -100 AND structural_alpha <= 100",
            name="future_assessment_alpha_range",
        ),
        sa.CheckConstraint(
            "confidence IN ('high', 'medium', 'low', 'insufficient')",
            name="future_assessment_confidence",
        ),
        sa.CheckConstraint(
            "calibration_state IN ('calibrated', 'partially_calibrated', 'uncalibrated')",
            name="future_assessment_calibration",
        ),
        sa.CheckConstraint(
            "confidence_score >= 0 AND confidence_score <= 1",
            name="future_assessment_confidence_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_future_assessment_listing",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_future_assessment"),
        sa.UniqueConstraint("future_assessment_version", name="uq_future_assessment_version"),
        sa.UniqueConstraint(
            "listing_id",
            "data_mode",
            "input_fingerprint",
            "future_model_version",
            "scenario_model_version",
            "configuration_version",
            name="uq_future_assessment_input_versions",
        ),
    )
    op.create_index(
        "ix_future_assessment_listing_id",
        "future_assessment",
        ["listing_id"],
    )
    op.create_index(
        "ix_future_assessment_listing_latest",
        "future_assessment",
        ["listing_id", "data_mode", "generated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_future_assessment_listing_latest", table_name="future_assessment")
    op.drop_index("ix_future_assessment_listing_id", table_name="future_assessment")
    op.drop_table("future_assessment")
    op.drop_index("ix_future_factor_scope_latest", table_name="future_factor_observation")
    op.drop_table("future_factor_observation")
    op.drop_index("idx_future_project_coordinates", table_name="future_project")
    op.drop_index("ix_future_project_scope", table_name="future_project")
    op.drop_table("future_project")
    op.drop_index("idx_employment_center_coordinates", table_name="employment_center")
    op.drop_index("ix_employment_center_mode_effective", table_name="employment_center")
    op.drop_table("employment_center")
