"""Add P5.6 real-world blind-validation gate.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.drop_constraint(
        "decision_validation_batch_status", "decision_validation_batch", type_="check"
    )
    op.create_check_constraint(
        "decision_validation_batch_status",
        "decision_validation_batch",
        "status IN ('blind_labeling', 'labels_frozen', 'model_generated', 'revealed', 'finalized')",
    )
    op.add_column(
        "decision_validation_batch",
        sa.Column("protocol_version", sa.String(16), server_default="p5.5", nullable=False),
    )
    op.add_column("decision_validation_batch", sa.Column("validation_run_id", sa.String(96)))
    op.add_column(
        "decision_validation_batch",
        sa.Column("freeze_manifest", _jsonb(), server_default="{}", nullable=False),
    )
    op.add_column("decision_validation_batch", sa.Column("dataset_fingerprint", sa.String(64)))
    op.add_column("decision_validation_batch", sa.Column("human_labels_fingerprint", sa.String(64)))
    op.add_column(
        "decision_validation_batch", sa.Column("model_outputs_fingerprint", sa.String(64))
    )
    op.add_column("decision_validation_batch", sa.Column("final_recommendation", sa.String(40)))
    op.add_column(
        "decision_validation_batch", sa.Column("labels_frozen_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "decision_validation_batch", sa.Column("model_generated_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "decision_validation_batch", sa.Column("finalized_at", sa.DateTime(timezone=True))
    )
    op.create_unique_constraint(
        "uq_decision_validation_run_id", "decision_validation_batch", ["validation_run_id"]
    )
    op.create_check_constraint(
        "decision_validation_batch_recommendation",
        "decision_validation_batch",
        "final_recommendation IS NULL OR final_recommendation IN "
        "('P6_READY', 'P6_READY_WITH_LIMITATIONS', 'P5_CALIBRATION_REQUIRED')",
    )

    op.alter_column("decision_validation_item", "decision_assessment_id", nullable=True)
    op.add_column("decision_validation_item", sa.Column("geography_bucket", sa.String(40)))
    op.add_column(
        "decision_validation_item",
        sa.Column("archetypes", _jsonb(), server_default="[]", nullable=False),
    )
    op.add_column(
        "decision_validation_item",
        sa.Column("data_provenance", _jsonb(), server_default="{}", nullable=False),
    )
    op.add_column(
        "decision_validation_item",
        sa.Column("human_opportunity_classification", sa.String(40)),
    )
    op.add_column(
        "decision_validation_item", sa.Column("human_workflow_recommendation", sa.String(16))
    )
    op.add_column("decision_validation_item", sa.Column("human_confidence", sa.String(16)))
    op.add_column(
        "decision_validation_item",
        sa.Column("human_positive_reasons", _jsonb(), server_default="[]", nullable=False),
    )
    op.add_column(
        "decision_validation_item",
        sa.Column("human_risks", _jsonb(), server_default="[]", nullable=False),
    )
    op.add_column("decision_validation_item", sa.Column("human_would_visit", sa.Boolean()))

    op.create_table(
        "decision_validation_review",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("why_ranked_verdict", sa.String(16), nullable=False),
        sa.Column("root_cause", sa.String(40)),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "why_ranked_verdict IN ('AGREE', 'PARTIAL', 'CONTRADICT')",
            name="decision_validation_review_verdict",
        ),
        sa.CheckConstraint(
            "root_cause IS NULL OR root_cause IN "
            "('DATA_GAP', 'BAD_COMPARABLE_SELECTION', 'VALUATION_ERROR', "
            "'LIQUIDITY_ERROR', 'FUTURE_FACTOR_ERROR', 'OBSOLESCENCE_ERROR', "
            "'DECISION_RULE_ERROR', 'HUMAN_DISAGREEMENT', 'UNKNOWN')",
            name="decision_validation_review_root_cause",
        ),
        sa.ForeignKeyConstraint(["item_id"], ["decision_validation_item.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", name="uq_decision_validation_review_item"),
    )
    op.create_table(
        "decision_validation_label_amendment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("proposed_label", _jsonb(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["item_id"], ["decision_validation_item.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("decision_validation_label_amendment")
    op.drop_table("decision_validation_review")
    for column in (
        "human_would_visit",
        "human_risks",
        "human_positive_reasons",
        "human_confidence",
        "human_workflow_recommendation",
        "human_opportunity_classification",
        "data_provenance",
        "archetypes",
        "geography_bucket",
    ):
        op.drop_column("decision_validation_item", column)
    op.alter_column("decision_validation_item", "decision_assessment_id", nullable=False)
    op.drop_constraint(
        "decision_validation_batch_recommendation", "decision_validation_batch", type_="check"
    )
    op.drop_constraint("uq_decision_validation_run_id", "decision_validation_batch", type_="unique")
    for column in (
        "finalized_at",
        "model_generated_at",
        "labels_frozen_at",
        "final_recommendation",
        "model_outputs_fingerprint",
        "human_labels_fingerprint",
        "dataset_fingerprint",
        "freeze_manifest",
        "validation_run_id",
        "protocol_version",
    ):
        op.drop_column("decision_validation_batch", column)
    op.drop_constraint(
        "decision_validation_batch_status", "decision_validation_batch", type_="check"
    )
    op.create_check_constraint(
        "decision_validation_batch_status",
        "decision_validation_batch",
        "status IN ('blind_labeling', 'revealed')",
    )
