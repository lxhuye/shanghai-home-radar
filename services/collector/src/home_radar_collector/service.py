from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic

from home_radar_models.collection import CrawlRun
from home_radar_models.enums import (
    CrawlCompleteness,
    CrawlRunStatus,
    DataMode,
    NormalizationStatus,
)
from home_radar_models.listing import Listing
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from home_radar_collector.contracts import SourceAdapter
from home_radar_collector.errors import (
    AuthenticationRequiredError,
    SourceIdentityMismatchError,
)
from home_radar_collector.repository import ListingRepository, ReconciliationResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollectionResult:
    run_id: uuid.UUID
    source: str
    scope_key: str
    status: str
    completeness: str
    raw_item_count: int
    normalized_item_count: int
    parse_error_count: int
    reconciliation_performed: bool
    listings_missing_candidate: int
    listings_inactivated: int
    coverage_guard_triggered: bool
    coverage_guard_reason: str | None
    previous_complete_count: int | None
    current_count_ratio: float | None

    @property
    def records_seen(self) -> int:
        return self.raw_item_count

    @property
    def records_ingested(self) -> int:
        return self.normalized_item_count

    @property
    def records_failed(self) -> int:
        return self.parse_error_count

    @property
    def listings_marked_missing(self) -> int:
        return self.listings_missing_candidate + self.listings_inactivated


class CollectorService:
    def __init__(
        self,
        session: Session,
        *,
        final_after_missing_runs: int = 2,
        data_mode: DataMode = DataMode.SAMPLE,
        min_complete_items: int = 1,
        min_complete_count_ratio: float = 0.5,
    ) -> None:
        if min_complete_items < 1:
            raise ValueError("min_complete_items must be at least 1")
        if not 0 < min_complete_count_ratio <= 1:
            raise ValueError("min_complete_count_ratio must be in (0, 1]")
        self.session = session
        self.repository = ListingRepository(session)
        self.final_after_missing_runs = final_after_missing_runs
        self.data_mode = data_mode
        self.min_complete_items = min_complete_items
        self.min_complete_count_ratio = min_complete_count_ratio

    async def collect(
        self, adapter: SourceAdapter, *, run_id: uuid.UUID | None = None
    ) -> CollectionResult:
        started_clock = monotonic()
        started_at = datetime.now(UTC)
        run_id = run_id or uuid.uuid4()
        existing = self.repository.get_run(run_id)
        if existing is not None and existing.status == CrawlRunStatus.SUCCEEDED.value:
            return self._result_from_run(existing)

        run = existing or CrawlRun(id=run_id)
        self._start_run(run, adapter=adapter, started_at=started_at, data_mode=self.data_mode)
        if existing is None:
            self.session.add(run)
        self.session.flush()

        if not self.repository.try_scope_lock(
            source=adapter.source_id, scope_key=adapter.scope_key
        ):
            run.status = CrawlRunStatus.ALREADY_RUNNING.value
            run.finished_at = datetime.now(UTC)
            run.error_type = "already_running"
            run.error_message = "another worker owns this source and scope"
            self.session.commit()
            result = self._result_from_run(run)
            self._log_run(result, started_clock=started_clock)
            return result

        try:
            fetch_result = await adapter.fetch()
            self._validate_fetch_identity(adapter, fetch_result.source_id, fetch_result.scope_key)
            run.raw_item_count = len(fetch_result.raw_items)
            run.run_metadata = fetch_result.metadata

            seen: dict[uuid.UUID, Listing] = {}
            for raw_item in fetch_result.raw_items:
                source_record_id = self._safe_source_record_id(adapter, raw_item)
                try:
                    observation = adapter.normalize(raw_item)
                except Exception as exc:
                    self.repository.add_raw_record(
                        crawl_run_id=run.id,
                        observed_at=datetime.now(UTC),
                        payload=raw_item,
                        source_record_id=source_record_id,
                        normalization_status=NormalizationStatus.FAILED,
                        normalization_error=self._error_text(exc),
                    )
                    run.parse_error_count += 1
                    continue

                if observation.source != fetch_result.source_id:
                    raise SourceIdentityMismatchError(
                        f"observation source {observation.source} does not match "
                        f"fetch source {fetch_result.source_id}"
                    )
                try:
                    with self.session.begin_nested():
                        ingestion = self.repository.ingest_observation(
                            crawl_run_id=run.id,
                            observation=observation,
                            raw_payload=raw_item,
                        )
                        self.repository.add_raw_record(
                            crawl_run_id=run.id,
                            observed_at=observation.observed_at,
                            payload=raw_item,
                            source_record_id=source_record_id,
                            normalization_status=NormalizationStatus.SUCCESS,
                            normalization_error=None,
                            listing_id=ingestion.listing.id,
                        )
                    seen[ingestion.listing.id] = ingestion.listing
                    run.normalized_item_count += 1
                except SQLAlchemyError as exc:
                    self.repository.add_raw_record(
                        crawl_run_id=run.id,
                        observed_at=observation.observed_at,
                        payload=raw_item,
                        source_record_id=source_record_id,
                        normalization_status=NormalizationStatus.FAILED,
                        normalization_error=f"ingestion_error: {exc.__class__.__name__}",
                    )
                    run.parse_error_count += 1

            completeness = self._final_completeness(
                declared=fetch_result.completeness,
                parse_error_count=run.parse_error_count,
            )
            run.completeness = self._apply_live_coverage_guard(run, completeness).value
            finished_at = datetime.now(UTC)
            complete = run.completeness == CrawlCompleteness.COMPLETE.value
            for listing in seen.values():
                self.repository.mark_observed_presence(
                    crawl_run_id=run.id,
                    listing=listing,
                    source=run.source,
                    scope_key=run.scope_key,
                    observed_at=finished_at,
                    complete=complete,
                )

            reconciliation = ReconciliationResult()
            if complete:
                reconciliation = self.repository.reconcile_complete_scope(
                    crawl_run_id=run.id,
                    source=run.source,
                    scope_key=run.scope_key,
                    observed_at=finished_at,
                    final_after_runs=self.final_after_missing_runs,
                    seen_listing_ids=set(seen),
                )
            self._finish_success(run, finished_at=finished_at, reconciliation=reconciliation)
            self.session.commit()
        except AuthenticationRequiredError as exc:
            self._finish_failure(
                run,
                adapter=adapter,
                started_at=started_at,
                status=CrawlRunStatus.PAUSED_AUTH,
                error=exc,
            )
            raise
        except Exception as exc:
            self._finish_failure(
                run,
                adapter=adapter,
                started_at=started_at,
                status=CrawlRunStatus.FAILED,
                error=exc,
            )
            raise

        result = self._result_from_run(run)
        self._log_run(result, started_clock=started_clock)
        return result

    @staticmethod
    def _start_run(
        run: CrawlRun,
        *,
        adapter: SourceAdapter,
        started_at: datetime,
        data_mode: DataMode,
    ) -> None:
        run.source = adapter.source_id
        run.data_mode = data_mode.value
        run.scope_key = adapter.scope_key
        run.status = CrawlRunStatus.RUNNING.value
        run.completeness = CrawlCompleteness.UNKNOWN.value
        run.started_at = started_at
        run.finished_at = None
        run.raw_item_count = 0
        run.normalized_item_count = 0
        run.parse_error_count = 0
        run.error_type = None
        run.error_message = None
        run.run_metadata = {}

    def _finish_failure(
        self,
        run: CrawlRun,
        *,
        adapter: SourceAdapter,
        started_at: datetime,
        status: CrawlRunStatus,
        error: Exception,
    ) -> None:
        self.session.rollback()
        failure = CrawlRun(
            id=run.id,
            source=adapter.source_id,
            data_mode=self.data_mode.value,
            scope_key=adapter.scope_key,
            status=status.value,
            completeness=CrawlCompleteness.UNKNOWN.value,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            error_type=error.__class__.__name__,
            error_message=str(error)[:4000],
            run_metadata={},
        )
        self.session.merge(failure)
        self.session.commit()
        logger.exception(
            "crawl_run_failed",
            extra={
                "run_id": str(run.id),
                "source": adapter.source_id,
                "scope_key": adapter.scope_key,
                "status": status.value,
                "error_type": error.__class__.__name__,
            },
        )

    @staticmethod
    def _finish_success(
        run: CrawlRun,
        *,
        finished_at: datetime,
        reconciliation: ReconciliationResult,
    ) -> None:
        run.status = CrawlRunStatus.SUCCEEDED.value
        run.finished_at = finished_at
        run.run_metadata = {
            **run.run_metadata,
            "reconciliation_performed": run.completeness == CrawlCompleteness.COMPLETE.value,
            "listings_missing_candidate": reconciliation.missing_candidates,
            "listings_inactivated": reconciliation.inactivated,
        }

    @staticmethod
    def _final_completeness(
        *, declared: CrawlCompleteness, parse_error_count: int
    ) -> CrawlCompleteness:
        if declared is CrawlCompleteness.COMPLETE and parse_error_count == 0:
            return CrawlCompleteness.COMPLETE
        return CrawlCompleteness.PARTIAL

    def _apply_live_coverage_guard(
        self, run: CrawlRun, completeness: CrawlCompleteness
    ) -> CrawlCompleteness:
        if self.data_mode is not DataMode.LIVE or completeness is not CrawlCompleteness.COMPLETE:
            return completeness
        previous = self.repository.latest_complete_run(
            source=run.source,
            scope_key=run.scope_key,
            data_mode=self.data_mode.value,
        )
        previous_count = previous.normalized_item_count if previous is not None else None
        reason, ratio = self._coverage_guard_reason(
            current_count=run.normalized_item_count,
            previous_count=previous_count,
        )
        run.run_metadata = {
            **run.run_metadata,
            "coverage_guard_triggered": reason is not None,
            "coverage_guard_reason": reason,
            "previous_complete_count": previous_count,
            "current_count_ratio": ratio,
            "minimum_complete_items": self.min_complete_items,
            "minimum_complete_count_ratio": self.min_complete_count_ratio,
        }
        return CrawlCompleteness.PARTIAL if reason is not None else completeness

    def _coverage_guard_reason(
        self, *, current_count: int, previous_count: int | None
    ) -> tuple[str | None, float | None]:
        if current_count < self.min_complete_items:
            return "below_minimum_item_count", None
        if previous_count is None or previous_count <= 0:
            return None, None
        ratio = current_count / previous_count
        if ratio < self.min_complete_count_ratio:
            return "unexpected_item_count_drop", ratio
        return None, ratio

    @staticmethod
    def _validate_fetch_identity(
        adapter: SourceAdapter, result_source: str, result_scope: str
    ) -> None:
        if adapter.source_id != result_source or adapter.scope_key != result_scope:
            raise SourceIdentityMismatchError(
                "adapter identity does not match its fetch result identity"
            )

    @staticmethod
    def _safe_source_record_id(adapter: SourceAdapter, raw_item: dict[str, object]) -> str | None:
        try:
            return adapter.source_record_id(raw_item)
        except Exception:
            return None

    @staticmethod
    def _error_text(error: Exception) -> str:
        return f"{error.__class__.__name__}: {error}"[:4000]

    @staticmethod
    def _result_from_run(run: CrawlRun) -> CollectionResult:
        metadata = run.run_metadata or {}
        return CollectionResult(
            run_id=run.id,
            source=run.source,
            scope_key=run.scope_key,
            status=run.status,
            completeness=run.completeness,
            raw_item_count=run.raw_item_count,
            normalized_item_count=run.normalized_item_count,
            parse_error_count=run.parse_error_count,
            reconciliation_performed=bool(metadata.get("reconciliation_performed", False)),
            listings_missing_candidate=int(metadata.get("listings_missing_candidate", 0)),
            listings_inactivated=int(metadata.get("listings_inactivated", 0)),
            coverage_guard_triggered=bool(metadata.get("coverage_guard_triggered", False)),
            coverage_guard_reason=metadata.get("coverage_guard_reason"),
            previous_complete_count=metadata.get("previous_complete_count"),
            current_count_ratio=metadata.get("current_count_ratio"),
        )

    @staticmethod
    def _log_run(result: CollectionResult, *, started_clock: float) -> None:
        logger.info(
            "crawl_run_finished",
            extra={
                "run_id": str(result.run_id),
                "source": result.source,
                "scope_key": result.scope_key,
                "raw_items": result.raw_item_count,
                "normalized_items": result.normalized_item_count,
                "parse_errors": result.parse_error_count,
                "completeness": result.completeness,
                "duration_seconds": round(monotonic() - started_clock, 3),
                "reconciliation_performed": result.reconciliation_performed,
                "listings_seen": result.normalized_item_count,
                "listings_missing_candidate": result.listings_missing_candidate,
                "listings_inactivated": result.listings_inactivated,
            },
        )
