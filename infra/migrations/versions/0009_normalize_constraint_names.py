"""Normalize constraint names that exceed PostgreSQL's identifier limit.

Revision ID: 0009
Revises: 0008
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RENAMES = (
    (
        "baseline_materialization_run",
        "ck_baseline_materialization_run_baseline_materializatio_52ea",
        "ck_baseline_materialization_run_data_mode",
    ),
    (
        "baseline_materialization_run",
        "ck_baseline_materialization_run_baseline_materializatio_70d0",
        "ck_baseline_materialization_run_status",
    ),
    (
        "decision_validation_batch",
        "ck_decision_validation_batch_decision_validation_batch__625f",
        "ck_decision_validation_batch_data_mode",
    ),
    (
        "decision_validation_batch",
        "ck_decision_validation_batch_decision_validation_batch__513f",
        "ck_decision_validation_batch_recommendation",
    ),
    (
        "decision_validation_item",
        "ck_decision_validation_item_decision_validation_item_hu_624c",
        "ck_decision_validation_item_human_label",
    ),
    (
        "decision_validation_item",
        "ck_decision_validation_item_decision_validation_item_po_8093",
        "ck_decision_validation_item_positive_rank",
    ),
    (
        "decision_validation_item",
        "ck_decision_validation_item_decision_validation_item_hu_b119",
        "ck_decision_validation_item_human_class",
    ),
    (
        "decision_validation_item",
        "ck_decision_validation_item_decision_validation_item_hu_3ba7",
        "ck_decision_validation_item_human_workflow",
    ),
    (
        "decision_validation_item",
        "ck_decision_validation_item_decision_validation_item_hu_d459",
        "ck_decision_validation_item_human_confidence",
    ),
    (
        "decision_validation_review",
        "ck_decision_validation_review_decision_validation_revie_6f01",
        "ck_decision_validation_review_verdict",
    ),
    (
        "decision_validation_review",
        "ck_decision_validation_review_decision_validation_revie_597d",
        "ck_decision_validation_review_root_cause",
    ),
    (
        "future_factor_observation",
        "ck_future_factor_observation_future_factor_observation__afd9",
        "ck_future_factor_observation_data_mode",
    ),
    (
        "future_factor_observation",
        "ck_future_factor_observation_future_factor_observation__f894",
        "ck_future_factor_observation_confidence_range",
    ),
    (
        "future_factor_observation",
        "ck_future_factor_observation_future_factor_observation__70a1",
        "ck_future_factor_observation_current_score_range",
    ),
    (
        "future_factor_observation",
        "ck_future_factor_observation_future_factor_observation__a2cd",
        "ck_future_factor_observation_future_score_range",
    ),
    (
        "market_observation",
        "ck_market_observation_market_observation_nonnegative_re_88d7",
        "ck_market_observation_nonnegative_rent_per_sqm",
    ),
)


def _rename(table: str, old_name: str, new_name: str) -> None:
    op.execute(f'ALTER TABLE "{table}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"')


def upgrade() -> None:
    for table, old_name, new_name in RENAMES:
        _rename(table, old_name, new_name)


def downgrade() -> None:
    for table, old_name, new_name in reversed(RENAMES):
        _rename(table, new_name, old_name)
