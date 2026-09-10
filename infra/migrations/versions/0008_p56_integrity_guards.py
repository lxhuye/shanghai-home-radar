"""Add database-level P5.6 immutability guards.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_check_constraint(
        "decision_validation_item_geography",
        "decision_validation_item",
        "geography_bucket IS NULL OR geography_bucket IN "
        "('OUTER_XUHUI', 'NORTHERN_MINHANG', 'PUTUO', 'YANGPU', 'MATURE_PUDONG')",
    )
    op.create_check_constraint(
        "decision_validation_item_human_class",
        "decision_validation_item",
        "human_opportunity_classification IS NULL OR "
        "human_opportunity_classification IN "
        "('QUALITY_AT_DISCOUNT', 'GOOD_BUT_EXPENSIVE', 'VALUE_TRAP', "
        "'LOW_QUALITY', 'INSUFFICIENT_INFORMATION')",
    )
    op.create_check_constraint(
        "decision_validation_item_human_workflow",
        "decision_validation_item",
        "human_workflow_recommendation IS NULL OR "
        "human_workflow_recommendation IN ('PASS', 'WATCH', 'CONTACT', 'VIEW')",
    )
    op.create_check_constraint(
        "decision_validation_item_human_confidence",
        "decision_validation_item",
        "human_confidence IS NULL OR human_confidence IN ('LOW', 'MEDIUM', 'HIGH')",
    )
    op.execute(
        """
        CREATE FUNCTION enforce_p56_validation_item_immutability()
        RETURNS trigger AS $$
        DECLARE
            batch_status text;
            batch_protocol text;
        BEGIN
            SELECT status, protocol_version INTO batch_status, batch_protocol
            FROM decision_validation_batch
            WHERE id = OLD.batch_id;

            IF batch_protocol IS DISTINCT FROM 'p5.6' THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;

            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'P5.6 validation items are immutable';
            END IF;

            IF NEW.batch_id IS DISTINCT FROM OLD.batch_id
               OR NEW.listing_id IS DISTINCT FROM OLD.listing_id
               OR NEW.listing_snapshot IS DISTINCT FROM OLD.listing_snapshot
               OR NEW.geography_bucket IS DISTINCT FROM OLD.geography_bucket
               OR NEW.archetypes IS DISTINCT FROM OLD.archetypes
               OR NEW.data_provenance IS DISTINCT FROM OLD.data_provenance THEN
                RAISE EXCEPTION 'P5.6 frozen dataset is immutable';
            END IF;

            IF batch_status IS DISTINCT FROM 'blind_labeling'
               AND (
                   NEW.human_opportunity_classification IS DISTINCT FROM
                       OLD.human_opportunity_classification
                   OR NEW.human_workflow_recommendation IS DISTINCT FROM
                       OLD.human_workflow_recommendation
                   OR NEW.human_confidence IS DISTINCT FROM OLD.human_confidence
                   OR NEW.human_positive_reasons IS DISTINCT FROM OLD.human_positive_reasons
                   OR NEW.human_risks IS DISTINCT FROM OLD.human_risks
                   OR NEW.human_would_visit IS DISTINCT FROM OLD.human_would_visit
                   OR NEW.human_label IS DISTINCT FROM OLD.human_label
                   OR NEW.notes IS DISTINCT FROM OLD.notes
                   OR NEW.labeled_at IS DISTINCT FROM OLD.labeled_at
               ) THEN
                RAISE EXCEPTION 'P5.6 frozen human labels are immutable';
            END IF;

            IF (
                NEW.decision_assessment_id IS DISTINCT FROM OLD.decision_assessment_id
                OR NEW.frozen_rank IS DISTINCT FROM OLD.frozen_rank
                OR NEW.model_snapshot IS DISTINCT FROM OLD.model_snapshot
            ) AND batch_status IS DISTINCT FROM 'labels_frozen' THEN
                RAISE EXCEPTION 'P5.6 frozen model output is immutable';
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER decision_validation_item_p56_immutable
        BEFORE UPDATE OR DELETE ON decision_validation_item
        FOR EACH ROW EXECUTE FUNCTION enforce_p56_validation_item_immutability();
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_p56_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'P5.6 review and amendment records are append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in (
        "decision_validation_review",
        "decision_validation_label_amendment",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_p56_append_only_mutation();
            """
        )


def downgrade() -> None:
    for table in (
        "decision_validation_label_amendment",
        "decision_validation_review",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_p56_append_only_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS decision_validation_item_p56_immutable ON decision_validation_item"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_p56_validation_item_immutability()")
    for constraint in (
        "decision_validation_item_human_confidence",
        "decision_validation_item_human_workflow",
        "decision_validation_item_human_class",
        "decision_validation_item_geography",
    ):
        op.drop_constraint(constraint, "decision_validation_item", type_="check")
