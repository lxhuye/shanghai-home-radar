"""Add the P4 fair value and value score result cache.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "valuation_result",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("data_mode", sa.String(length=16), nullable=False),
        sa.Column("valuation_version", sa.String(length=96), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fair_value", sa.Numeric(14, 2), nullable=False),
        sa.Column("fair_value_low", sa.Numeric(14, 2), nullable=False),
        sa.Column("fair_value_high", sa.Numeric(14, 2), nullable=False),
        sa.Column("valuation_basis", sa.String(length=32), nullable=False),
        sa.Column("valuation_confidence_score", sa.Numeric(7, 6), nullable=False),
        sa.Column("valuation_confidence", sa.String(length=24), nullable=False),
        sa.Column("transaction_support", sa.String(length=16), nullable=False),
        sa.Column("current_ask", sa.Numeric(14, 2), nullable=False),
        sa.Column("ask_discount_to_fair_value", sa.Numeric(9, 6), nullable=False),
        sa.Column("estimated_executable_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("executable_discount_to_fair_value", sa.Numeric(9, 6), nullable=True),
        sa.Column("value_score", sa.Numeric(6, 2), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("comparable_count", sa.Integer(), nullable=False),
        sa.Column("effective_comparable_count", sa.Numeric(10, 4), nullable=False),
        sa.Column("baseline_level_used", sa.String(length=48), nullable=False),
        sa.Column("baseline_confidence", sa.String(length=24), nullable=False),
        sa.Column("baseline_version", sa.String(length=256), nullable=False),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        sa.Column("adjustments", _jsonb(), server_default="[]", nullable=False),
        sa.Column("comparables", _jsonb(), server_default="[]", nullable=False),
        sa.Column("warnings", _jsonb(), server_default="[]", nullable=False),
        sa.Column("score_components", _jsonb(), server_default="{}", nullable=False),
        sa.Column("price_history", _jsonb(), server_default="{}", nullable=False),
        sa.Column("provenance", _jsonb(), server_default="{}", nullable=False),
        sa.Column("valuation_model_version", sa.String(length=40), nullable=False),
        sa.Column("scoring_model_version", sa.String(length=40), nullable=False),
        sa.Column("configuration_version", sa.String(length=80), nullable=False),
        sa.Column("baseline_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_mode IN ('demo', 'sample', 'live')", name="valuation_result_data_mode"
        ),
        sa.CheckConstraint(
            "valuation_basis IN ('listing_derived', 'transaction_supported', 'mixed_source')",
            name="valuation_result_basis",
        ),
        sa.CheckConstraint(
            "valuation_confidence IN ('high', 'medium', 'low', 'insufficient')",
            name="valuation_result_confidence",
        ),
        sa.CheckConstraint(
            "transaction_support IN ('none', 'weak', 'strong')",
            name="valuation_result_transaction_support",
        ),
        sa.CheckConstraint(
            "decision IN ('pass', 'watch', 'contact', 'view', 'attack')",
            name="valuation_result_decision",
        ),
        sa.CheckConstraint(
            "fair_value > 0 AND fair_value_low > 0 AND fair_value_high > 0 "
            "AND fair_value_low <= fair_value AND fair_value <= fair_value_high",
            name="valuation_result_value_range",
        ),
        sa.CheckConstraint("current_ask > 0", name="valuation_result_positive_current_ask"),
        sa.CheckConstraint(
            "estimated_executable_price IS NULL OR estimated_executable_price > 0",
            name="valuation_result_positive_executable_price",
        ),
        sa.CheckConstraint(
            "valuation_confidence_score >= 0 AND valuation_confidence_score <= 1",
            name="valuation_result_confidence_score_range",
        ),
        sa.CheckConstraint(
            "value_score >= 0 AND value_score <= 100",
            name="valuation_result_value_score_range",
        ),
        sa.CheckConstraint(
            "comparable_count >= 0 AND effective_comparable_count >= 0",
            name="valuation_result_nonnegative_comparables",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_valuation_result_listing",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_valuation_result"),
        sa.UniqueConstraint("valuation_version", name="uq_valuation_result_version"),
        sa.UniqueConstraint(
            "listing_id",
            "data_mode",
            "input_fingerprint",
            "valuation_model_version",
            "scoring_model_version",
            "configuration_version",
            name="uq_valuation_result_input_versions",
        ),
    )
    op.create_index("ix_valuation_result_listing_id", "valuation_result", ["listing_id"])
    op.create_index(
        "ix_valuation_result_listing_latest",
        "valuation_result",
        ["listing_id", "data_mode", "calculated_at"],
    )
    op.create_index(
        "ix_valuation_result_mode_decision_score",
        "valuation_result",
        ["data_mode", "decision", "value_score"],
    )


def downgrade() -> None:
    op.drop_index("ix_valuation_result_mode_decision_score", table_name="valuation_result")
    op.drop_index("ix_valuation_result_listing_latest", table_name="valuation_result")
    op.drop_index("ix_valuation_result_listing_id", table_name="valuation_result")
    op.drop_table("valuation_result")
