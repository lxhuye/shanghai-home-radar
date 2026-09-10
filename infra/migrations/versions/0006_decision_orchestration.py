"""Add P5.5 decision orchestration and blind-validation records.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    _create_decision_assessment()
    _create_validation_batch()
    _create_validation_item()


def _create_decision_assessment() -> None:
    op.create_table(
        "decision_assessment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("decision_version", sa.String(length=96), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_ask", sa.Numeric(14, 2), nullable=False),
        sa.Column("fair_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("fair_value_low", sa.Numeric(14, 2), nullable=False),
        sa.Column("fair_value_high", sa.Numeric(14, 2), nullable=False),
        sa.Column("value_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("future_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("liquidity_score", sa.Numeric(7, 4), nullable=True),
        sa.Column("obsolescence_risk", sa.Numeric(6, 2), nullable=True),
        sa.Column("structural_alpha", sa.Numeric(7, 2), nullable=False),
        sa.Column("valuation_confidence", sa.String(length=24), nullable=False),
        sa.Column("future_confidence", sa.String(length=24), nullable=False),
        sa.Column("calibration_state", sa.String(length=32), nullable=False),
        sa.Column("opportunity_classification", sa.String(length=40), nullable=False),
        sa.Column("workflow_state", sa.String(length=16), nullable=False),
        sa.Column("eligibility_status", sa.String(length=32), nullable=False),
        sa.Column("confidence_gate_passed", sa.Boolean(), nullable=False),
        sa.Column("hard_risks", _jsonb(), server_default="[]", nullable=False),
        sa.Column("positive_reasons", _jsonb(), server_default="[]", nullable=False),
        sa.Column("negative_reasons", _jsonb(), server_default="[]", nullable=False),
        sa.Column("warnings", _jsonb(), server_default="[]", nullable=False),
        sa.Column("ranking_dimensions", _jsonb(), server_default="{}", nullable=False),
        sa.Column("provenance", _jsonb(), server_default="{}", nullable=False),
        sa.Column("valuation_version", sa.String(length=96), nullable=False),
        sa.Column("future_assessment_version", sa.String(length=96), nullable=False),
        sa.Column("baseline_version", sa.String(length=256), nullable=False),
        sa.Column("decision_model_version", sa.String(length=40), nullable=False),
        sa.Column("configuration_version", sa.String(length=80), nullable=False),
        sa.Column("data_version", sa.String(length=96), nullable=False),
        sa.Column("data_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="decision_assessment_data_mode",
        ),
        sa.CheckConstraint(
            "opportunity_classification IN "
            "('QUALITY_AT_DISCOUNT', 'GOOD_BUT_EXPENSIVE', 'VALUE_TRAP', "
            "'LOW_QUALITY', 'INSUFFICIENT_DATA')",
            name="decision_assessment_classification",
        ),
        sa.CheckConstraint(
            "workflow_state IN ('PASS', 'WATCH', 'CONTACT', 'VIEW')",
            name="decision_assessment_workflow",
        ),
        sa.CheckConstraint(
            "eligibility_status IN ('ELIGIBLE', 'HARD_RISK_FILTERED', 'INSUFFICIENT')",
            name="decision_assessment_eligibility",
        ),
        sa.CheckConstraint(
            "value_score >= 0 AND value_score <= 100",
            name="decision_assessment_value_score_range",
        ),
        sa.CheckConstraint(
            "future_score IS NULL OR (future_score >= 0 AND future_score <= 100)",
            name="decision_assessment_future_score_range",
        ),
        sa.CheckConstraint(
            "liquidity_score IS NULL OR (liquidity_score >= 0 AND liquidity_score <= 100)",
            name="decision_assessment_liquidity_range",
        ),
        sa.CheckConstraint(
            "obsolescence_risk IS NULL OR (obsolescence_risk >= 0 AND obsolescence_risk <= 100)",
            name="decision_assessment_obsolescence_range",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_decision_assessment_listing",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decision_assessment"),
        sa.UniqueConstraint("decision_version", name="uq_decision_assessment_version"),
        sa.UniqueConstraint(
            "listing_id",
            "data_mode",
            "input_fingerprint",
            "decision_model_version",
            "configuration_version",
            name="uq_decision_assessment_input_versions",
        ),
    )
    op.create_index(
        "ix_decision_assessment_listing_id",
        "decision_assessment",
        ["listing_id"],
    )
    op.create_index(
        "ix_decision_assessment_listing_latest",
        "decision_assessment",
        ["listing_id", "data_mode", "generated_at"],
    )
    op.create_index(
        "ix_decision_assessment_opportunity",
        "decision_assessment",
        ["data_mode", "eligibility_status", "opportunity_classification"],
    )
    op.create_index(
        "ix_decision_assessment_rank_universe",
        "decision_assessment",
        [
            "data_mode",
            "decision_model_version",
            "configuration_version",
            "generated_at",
        ],
    )


def _create_validation_batch() -> None:
    op.create_table(
        "decision_validation_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("target_sample_size", sa.Integer(), nullable=False),
        sa.Column("input_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decision_model_version", sa.String(length=40), nullable=False),
        sa.Column("configuration_version", sa.String(length=80), nullable=False),
        sa.Column("metrics", _jsonb(), server_default="{}", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("revealed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')",
            name="decision_validation_batch_data_mode",
        ),
        sa.CheckConstraint(
            "status IN ('blind_labeling', 'revealed')",
            name="decision_validation_batch_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decision_validation_batch"),
    )


def _create_validation_item() -> None:
    op.create_table(
        "decision_validation_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("decision_assessment_id", sa.Uuid(), nullable=False),
        sa.Column("frozen_rank", sa.Integer(), nullable=True),
        sa.Column("listing_snapshot", _jsonb(), server_default="{}", nullable=False),
        sa.Column("model_snapshot", _jsonb(), server_default="{}", nullable=False),
        sa.Column("human_label", sa.String(length=24), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("labeled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "human_label IS NULL OR human_label IN ('WORTH_VIEWING', 'WAIT', 'VALUE_TRAP', 'PASS')",
            name="decision_validation_item_human_label",
        ),
        sa.CheckConstraint(
            "frozen_rank IS NULL OR frozen_rank > 0",
            name="decision_validation_item_positive_rank",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["decision_validation_batch.id"],
            name="fk_decision_validation_item_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_decision_validation_item_listing",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_assessment_id"],
            ["decision_assessment.id"],
            name="fk_decision_validation_item_assessment",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_decision_validation_item"),
        sa.UniqueConstraint("batch_id", "listing_id", name="uq_decision_validation_batch_listing"),
    )
    op.create_index(
        "ix_decision_validation_item_batch_rank",
        "decision_validation_item",
        ["batch_id", "frozen_rank"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_decision_validation_item_batch_rank",
        table_name="decision_validation_item",
    )
    op.drop_table("decision_validation_item")
    op.drop_table("decision_validation_batch")
    op.drop_index(
        "ix_decision_assessment_rank_universe",
        table_name="decision_assessment",
        if_exists=True,
    )
    op.drop_index("ix_decision_assessment_opportunity", table_name="decision_assessment")
    op.drop_index("ix_decision_assessment_listing_latest", table_name="decision_assessment")
    op.drop_index("ix_decision_assessment_listing_id", table_name="decision_assessment")
    op.drop_table("decision_assessment")
