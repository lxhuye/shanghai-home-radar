"""Harden crawl provenance, presence tracking, and append-only history.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _drop_append_only_triggers() -> None:
    op.execute("DROP TRIGGER listing_snapshot_append_only ON listing_snapshot")
    op.execute("DROP TRIGGER raw_source_record_append_only ON raw_source_record")


def _create_append_only_triggers(*, include_delete: bool) -> None:
    events = "UPDATE OR DELETE" if include_delete else "UPDATE"
    op.execute(
        f"""
        CREATE TRIGGER listing_snapshot_append_only
        BEFORE {events} ON listing_snapshot
        FOR EACH ROW EXECUTE FUNCTION reject_historical_update()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER raw_source_record_append_only
        BEFORE {events} ON raw_source_record
        FOR EACH ROW EXECUTE FUNCTION reject_historical_update()
        """
    )


def upgrade() -> None:
    _drop_append_only_triggers()

    op.rename_table("collection_run", "crawl_run")
    op.execute("ALTER TABLE crawl_run RENAME CONSTRAINT pk_collection_run TO pk_crawl_run")
    op.drop_index("ix_collection_run_source_started", table_name="crawl_run")

    op.alter_column("crawl_run", "records_seen", new_column_name="raw_item_count")
    op.alter_column("crawl_run", "records_ingested", new_column_name="normalized_item_count")
    op.alter_column("crawl_run", "records_failed", new_column_name="parse_error_count")
    op.add_column("crawl_run", sa.Column("scope_key", sa.String(length=255), nullable=True))
    op.add_column("crawl_run", sa.Column("completeness", sa.String(length=24), nullable=True))
    op.add_column("crawl_run", sa.Column("error_type", sa.String(length=160), nullable=True))
    op.add_column(
        "crawl_run",
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column("crawl_run", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE crawl_run
        SET scope_key = 'default',
            completeness = 'unknown',
            metadata = '{}'::jsonb,
            created_at = started_at
        """
    )
    op.alter_column("crawl_run", "scope_key", nullable=False, server_default=sa.text("'default'"))
    op.alter_column(
        "crawl_run", "completeness", nullable=False, server_default=sa.text("'unknown'")
    )
    op.alter_column("crawl_run", "metadata", nullable=False, server_default=sa.text("'{}'::jsonb"))
    op.alter_column("crawl_run", "created_at", nullable=False, server_default=sa.text("now()"))
    op.create_index(
        "ix_crawl_run_source_scope_started",
        "crawl_run",
        ["source", "scope_key", "started_at"],
    )

    op.drop_index("ix_raw_record_run_external", table_name="raw_source_record")
    op.drop_constraint(
        "fk_raw_source_record_run_id_collection_run", "raw_source_record", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_raw_source_record_listing_id_listing", "raw_source_record", type_="foreignkey"
    )
    op.alter_column("raw_source_record", "run_id", new_column_name="crawl_run_id")
    op.alter_column("raw_source_record", "source_listing_id", new_column_name="source_record_id")
    op.add_column(
        "raw_source_record",
        sa.Column("normalization_status", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "raw_source_record", sa.Column("created_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        """
        UPDATE raw_source_record
        SET normalization_status = CASE
                WHEN normalization_error IS NULL THEN 'success'
                ELSE 'failed'
            END,
            created_at = observed_at
        """
    )
    op.alter_column(
        "raw_source_record",
        "normalization_status",
        nullable=False,
        server_default=sa.text("'pending'"),
    )
    op.alter_column(
        "raw_source_record", "created_at", nullable=False, server_default=sa.text("now()")
    )
    op.create_foreign_key(
        "fk_raw_source_record_crawl_run_id_crawl_run",
        "raw_source_record",
        "crawl_run",
        ["crawl_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_raw_source_record_listing_id_listing",
        "raw_source_record",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_raw_record_crawl_run_source_record",
        "raw_source_record",
        ["crawl_run_id", "source_record_id"],
    )

    op.drop_constraint(
        "fk_listing_snapshot_listing_id_listing", "listing_snapshot", type_="foreignkey"
    )
    op.add_column("listing_snapshot", sa.Column("crawl_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_listing_snapshot_listing_id_listing",
        "listing_snapshot",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_listing_snapshot_crawl_run_id_crawl_run",
        "listing_snapshot",
        "crawl_run",
        ["crawl_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_snapshot_crawl_run_listing",
        "listing_snapshot",
        ["crawl_run_id", "listing_id"],
    )

    op.drop_constraint("fk_listing_event_listing_id_listing", "listing_event", type_="foreignkey")
    op.create_foreign_key(
        "fk_listing_event_listing_id_listing",
        "listing_event",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("listing_event", sa.Column("crawl_run_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_listing_event_crawl_run_id_crawl_run",
        "listing_event",
        "crawl_run",
        ["crawl_run_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_listing_event_crawl_run_id", "listing_event", ["crawl_run_id"])
    op.drop_constraint("fk_inquiry_listing_id_listing", "inquiry", type_="foreignkey")
    op.create_foreign_key(
        "fk_inquiry_listing_id_listing",
        "inquiry",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "listing_presence",
        sa.Column("listing_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("scope_key", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("consecutive_complete_misses", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_seen_complete_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "consecutive_complete_misses >= 0",
            name="nonnegative_complete_misses",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_listing_presence_listing_id_listing",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("listing_id", "source", "scope_key", name="pk_listing_presence"),
    )
    op.create_index(
        "ix_listing_presence_scope_state",
        "listing_presence",
        ["source", "scope_key", "state"],
    )
    op.execute(
        """
        INSERT INTO listing_presence (
            listing_id,
            source,
            scope_key,
            state,
            consecutive_complete_misses,
            last_seen_complete_run_at,
            created_at,
            updated_at
        )
        SELECT
            id,
            source,
            'default',
            CASE status
                WHEN 'temporarily_unavailable' THEN 'missing_candidate'
                WHEN 'removed' THEN 'inactive'
                ELSE status
            END,
            consecutive_missing_runs,
            CASE WHEN status = 'active' THEN last_seen_at ELSE NULL END,
            created_at,
            updated_at
        FROM listing
        """
    )

    op.execute(
        """
        UPDATE listing
        SET status = CASE status
            WHEN 'temporarily_unavailable' THEN 'missing_candidate'
            WHEN 'removed' THEN 'inactive'
            ELSE status
        END
        """
    )
    op.execute(
        """
        UPDATE listing_snapshot
        SET status = CASE status
            WHEN 'temporarily_unavailable' THEN 'missing_candidate'
            WHEN 'removed' THEN 'inactive'
            ELSE status
        END
        """
    )
    op.execute(
        """
        UPDATE listing_event
        SET event_type = CASE event_type
                WHEN 'temporary_disappearance' THEN 'missing_candidate'
                WHEN 'final_disappearance' THEN 'inactivated'
                ELSE event_type
            END,
            previous_value = CASE previous_value
                WHEN 'temporarily_unavailable' THEN 'missing_candidate'
                WHEN 'removed' THEN 'inactive'
                ELSE previous_value
            END,
            current_value = CASE current_value
                WHEN 'temporarily_unavailable' THEN 'missing_candidate'
                WHEN 'removed' THEN 'inactive'
                ELSE current_value
            END
        """
    )
    op.drop_column("listing", "consecutive_missing_runs")
    op.drop_column("listing", "missing_since_at")

    _create_append_only_triggers(include_delete=True)


def downgrade() -> None:
    _drop_append_only_triggers()

    op.add_column(
        "listing", sa.Column("missing_since_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "listing",
        sa.Column("consecutive_missing_runs", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        """
        UPDATE listing AS listing_row
        SET consecutive_missing_runs = presence.consecutive_complete_misses,
            missing_since_at = CASE
                WHEN presence.state IN ('missing_candidate', 'inactive') THEN presence.updated_at
                ELSE NULL
            END
        FROM listing_presence AS presence
        WHERE presence.listing_id = listing_row.id
          AND presence.source = listing_row.source
          AND presence.scope_key = 'default'
        """
    )

    op.execute(
        """
        UPDATE listing
        SET status = CASE status
            WHEN 'missing_candidate' THEN 'temporarily_unavailable'
            WHEN 'inactive' THEN 'removed'
            ELSE status
        END
        """
    )
    op.execute(
        """
        UPDATE listing_snapshot
        SET status = CASE status
            WHEN 'missing_candidate' THEN 'temporarily_unavailable'
            WHEN 'inactive' THEN 'removed'
            ELSE status
        END
        """
    )
    op.execute(
        """
        UPDATE listing_event
        SET event_type = CASE event_type
                WHEN 'missing_candidate' THEN 'temporary_disappearance'
                WHEN 'inactivated' THEN 'final_disappearance'
                ELSE event_type
            END,
            previous_value = CASE previous_value
                WHEN 'missing_candidate' THEN 'temporarily_unavailable'
                WHEN 'inactive' THEN 'removed'
                ELSE previous_value
            END,
            current_value = CASE current_value
                WHEN 'missing_candidate' THEN 'temporarily_unavailable'
                WHEN 'inactive' THEN 'removed'
                ELSE current_value
            END
        """
    )

    op.drop_index("ix_listing_presence_scope_state", table_name="listing_presence")
    op.drop_table("listing_presence")

    op.drop_constraint("fk_inquiry_listing_id_listing", "inquiry", type_="foreignkey")
    op.create_foreign_key(
        "fk_inquiry_listing_id_listing",
        "inquiry",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_constraint("fk_listing_event_listing_id_listing", "listing_event", type_="foreignkey")
    op.drop_index("ix_listing_event_crawl_run_id", table_name="listing_event")
    op.drop_constraint(
        "fk_listing_event_crawl_run_id_crawl_run", "listing_event", type_="foreignkey"
    )
    op.drop_column("listing_event", "crawl_run_id")
    op.create_foreign_key(
        "fk_listing_event_listing_id_listing",
        "listing_event",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint("uq_snapshot_crawl_run_listing", "listing_snapshot", type_="unique")
    op.drop_constraint(
        "fk_listing_snapshot_crawl_run_id_crawl_run",
        "listing_snapshot",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_listing_snapshot_listing_id_listing", "listing_snapshot", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_listing_snapshot_listing_id_listing",
        "listing_snapshot",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("listing_snapshot", "crawl_run_id")

    op.drop_index("ix_raw_record_crawl_run_source_record", table_name="raw_source_record")
    op.drop_constraint(
        "fk_raw_source_record_crawl_run_id_crawl_run",
        "raw_source_record",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_raw_source_record_listing_id_listing", "raw_source_record", type_="foreignkey"
    )
    op.drop_column("raw_source_record", "created_at")
    op.drop_column("raw_source_record", "normalization_status")
    op.alter_column("raw_source_record", "source_record_id", new_column_name="source_listing_id")
    op.alter_column("raw_source_record", "crawl_run_id", new_column_name="run_id")
    op.create_foreign_key(
        "fk_raw_source_record_listing_id_listing",
        "raw_source_record",
        "listing",
        ["listing_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.drop_index("ix_crawl_run_source_scope_started", table_name="crawl_run")
    op.drop_column("crawl_run", "created_at")
    op.drop_column("crawl_run", "metadata")
    op.drop_column("crawl_run", "error_type")
    op.drop_column("crawl_run", "completeness")
    op.drop_column("crawl_run", "scope_key")
    op.alter_column("crawl_run", "parse_error_count", new_column_name="records_failed")
    op.alter_column("crawl_run", "normalized_item_count", new_column_name="records_ingested")
    op.alter_column("crawl_run", "raw_item_count", new_column_name="records_seen")
    op.execute("ALTER TABLE crawl_run RENAME CONSTRAINT pk_crawl_run TO pk_collection_run")
    op.rename_table("crawl_run", "collection_run")

    op.create_foreign_key(
        "fk_raw_source_record_run_id_collection_run",
        "raw_source_record",
        "collection_run",
        ["run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_collection_run_source_started", "collection_run", ["source", "started_at"])
    op.create_index(
        "ix_raw_record_run_external",
        "raw_source_record",
        ["run_id", "source_listing_id"],
    )

    _create_append_only_triggers(include_delete=False)
