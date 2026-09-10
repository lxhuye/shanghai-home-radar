from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from home_radar_collector.adapters import ExampleJsonFeedAdapter, PartnerCsvFeedAdapter
from home_radar_collector.contracts import CrawlScope, FetchResult, ListingObservation
from home_radar_collector.errors import (
    AuthenticationRequiredError,
    SourceIdentityMismatchError,
    SourcePayloadValidationError,
)
from home_radar_collector.feed import ProviderCapabilities
from home_radar_collector.service import CollectorService
from home_radar_models.collection import CrawlRun, RawSourceRecord
from home_radar_models.enums import (
    CrawlCompleteness,
    CrawlRunStatus,
    DataMode,
    ListingEventType,
    ListingStatus,
    NormalizationStatus,
)
from home_radar_models.listing import Listing, ListingEvent, ListingPresence, ListingSnapshot
from sqlalchemy import Engine, delete, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session, sessionmaker

pytestmark = pytest.mark.integration


def item(
    listing_id: str,
    *,
    price_wan: int = 300,
    district: str = "Xuhui",
    observed_at: str | None = None,
) -> dict[str, object]:
    value: dict[str, object] = {
        "listing_id": listing_id,
        "url": f"https://example.invalid/{listing_id}",
        "district": district,
        "submarket": "Huajing" if district == "Xuhui" else "Zhenru",
        "community": f"Community {listing_id}",
        "price_wan": price_wan,
        "area_sqm": 60,
        "status": "active",
    }
    if observed_at:
        value["observed_at"] = observed_at
    return value


def file_adapter(
    tmp_path: Path,
    payload: object,
    *,
    district: str = "xuhui",
    name: str | None = None,
) -> ExampleJsonFeedAdapter:
    if isinstance(payload, dict) and "items" in payload and "schema_version" not in payload:
        values = payload["items"]
        item_count = len(values) if isinstance(values, list) else 0
        payload = {
            "schema_version": "1.0",
            "source_id": "sample_json",
            "scope": {"city": "shanghai", "district": district, "filters": {}},
            "completeness": payload.get("completeness", "complete"),
            "coverage": {
                "page_count": 1,
                "pages_fetched": 1,
                "reported_total": item_count,
                "items_returned": item_count,
            },
            "metadata": payload.get("metadata", {}),
            "items": values,
        }
    path = tmp_path / f"{name or uuid.uuid4().hex}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return ExampleJsonFeedAdapter(
        source_id="sample_json",
        endpoint=str(path),
        scope=CrawlScope(city="shanghai", district=district),
    )


async def collect(
    session: Session,
    adapter: ExampleJsonFeedAdapter,
    *,
    run_id: uuid.UUID | None = None,
) -> object:
    return await CollectorService(session, final_after_missing_runs=2).collect(
        adapter, run_id=run_id
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("malformed", [{}, {"error": "blocked"}])
async def test_malformed_payload_fails_without_reconciliation(
    db_session: Session, tmp_path: Path, malformed: object
) -> None:
    source = file_adapter(tmp_path, {"items": [item("a"), item("b")]})
    await collect(db_session, source)

    with pytest.raises(SourcePayloadValidationError):
        await collect(db_session, file_adapter(tmp_path, malformed))

    statuses = set(db_session.scalars(select(Listing.status)).all())
    failed = db_session.scalar(
        select(CrawlRun).where(CrawlRun.status == CrawlRunStatus.FAILED.value)
    )
    assert statuses == {ListingStatus.ACTIVE.value}
    assert failed is not None
    assert failed.completeness == CrawlCompleteness.UNKNOWN.value


@pytest.mark.asyncio
async def test_one_bad_item_makes_run_partial_and_preserves_presence(
    db_session: Session, tmp_path: Path
) -> None:
    source = file_adapter(tmp_path, {"items": [item("a"), item("b")]})
    await collect(db_session, source)
    partial = file_adapter(
        tmp_path,
        {"items": [item("a"), {"listing_id": "b"}]},
        name="partial",
    )

    result = await collect(db_session, partial)
    listing_b = db_session.scalar(select(Listing).where(Listing.source_listing_id == "b"))
    raw_statuses = set(db_session.scalars(select(RawSourceRecord.normalization_status)).all())

    assert result.completeness == CrawlCompleteness.PARTIAL.value
    assert result.parse_error_count == 1
    assert listing_b is not None and listing_b.status == ListingStatus.ACTIVE.value
    assert raw_statuses == {
        NormalizationStatus.SUCCESS.value,
        NormalizationStatus.FAILED.value,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("coverage", "reason"),
    [
        (
            {"page_count": 2, "pages_fetched": 1, "reported_total": 1, "items_returned": 1},
            "pagination_incomplete",
        ),
        (
            {"page_count": 1, "pages_fetched": 1, "reported_total": 2, "items_returned": 1},
            "reported_total_mismatch",
        ),
        (None, "coverage_missing"),
    ],
)
async def test_unproven_coverage_never_reconciles_presence(
    db_session: Session,
    tmp_path: Path,
    coverage: object,
    reason: str,
) -> None:
    await collect(db_session, file_adapter(tmp_path, {"items": [item("a"), item("b")]}))
    payload = {
        "schema_version": "1.0",
        "source_id": "sample_json",
        "scope": {"city": "shanghai", "district": "xuhui", "filters": {}},
        "completeness": "complete",
        "coverage": coverage,
        "metadata": {},
        "items": [item("a")],
    }

    result = await collect(
        db_session,
        file_adapter(tmp_path, payload, name=f"coverage-{reason}"),
    )
    listing_b = db_session.scalar(select(Listing).where(Listing.source_listing_id == "b"))
    latest_run = db_session.scalar(select(CrawlRun).order_by(CrawlRun.started_at.desc()))

    assert result.completeness == CrawlCompleteness.PARTIAL.value
    assert result.reconciliation_performed is False
    assert listing_b is not None and listing_b.status == ListingStatus.ACTIVE.value
    assert latest_run is not None
    assert reason in latest_run.run_metadata["coverage_reasons"]


@pytest.mark.asyncio
async def test_partial_run_cannot_apply_source_inactive_status(
    db_session: Session, tmp_path: Path
) -> None:
    await collect(db_session, file_adapter(tmp_path, {"items": [item("a"), item("b")]}))
    explicitly_inactive = item("a")
    explicitly_inactive["status"] = "inactive"

    result = await collect(
        db_session,
        file_adapter(
            tmp_path,
            {"completeness": "partial", "items": [explicitly_inactive]},
            name="declared-partial",
        ),
    )

    statuses = dict(db_session.execute(select(Listing.source_listing_id, Listing.status)).all())
    assert result.completeness == CrawlCompleteness.PARTIAL.value
    assert statuses == {
        "a": ListingStatus.ACTIVE.value,
        "b": ListingStatus.ACTIVE.value,
    }


@pytest.mark.asyncio
async def test_complete_empty_runs_drive_safe_lifecycle_and_relisting(
    db_session: Session, tmp_path: Path
) -> None:
    source = file_adapter(tmp_path, {"items": [item("a"), item("b")]})
    await collect(db_session, source)
    await collect(db_session, file_adapter(tmp_path, {"items": [item("a")]}, name="miss-1"))

    listing_b = db_session.scalar(select(Listing).where(Listing.source_listing_id == "b"))
    assert listing_b is not None
    assert listing_b.status == ListingStatus.MISSING_CANDIDATE.value

    await collect(db_session, file_adapter(tmp_path, {"items": [item("a")]}, name="miss-2"))
    db_session.refresh(listing_b)
    assert listing_b.status == ListingStatus.INACTIVE.value

    await collect(
        db_session,
        file_adapter(tmp_path, {"items": [item("a"), item("b")]}, name="return"),
    )
    db_session.refresh(listing_b)
    event_types = set(
        db_session.scalars(
            select(ListingEvent.event_type).where(ListingEvent.listing_id == listing_b.id)
        ).all()
    )
    assert listing_b.status == ListingStatus.ACTIVE.value
    assert ListingEventType.MISSING_CANDIDATE.value in event_types
    assert ListingEventType.INACTIVATED.value in event_types
    assert ListingEventType.RELISTED.value in event_types


@pytest.mark.asyncio
async def test_reconciliation_is_limited_to_exact_scope(
    db_session: Session, tmp_path: Path
) -> None:
    await collect(
        db_session,
        file_adapter(tmp_path, {"items": [item("xh")]}, district="xuhui", name="xh"),
    )
    await collect(
        db_session,
        file_adapter(
            tmp_path,
            {"items": [item("pt", district="Putuo")]},
            district="putuo",
            name="pt",
        ),
    )
    await collect(
        db_session,
        file_adapter(tmp_path, {"items": []}, district="xuhui", name="xh-empty"),
    )

    statuses = dict(db_session.execute(select(Listing.source_listing_id, Listing.status)).all())
    assert statuses == {
        "xh": ListingStatus.MISSING_CANDIDATE.value,
        "pt": ListingStatus.ACTIVE.value,
    }


@pytest.mark.asyncio
async def test_duplicate_run_id_does_not_duplicate_snapshot(
    db_session: Session, tmp_path: Path
) -> None:
    run_id = uuid.uuid4()
    source = file_adapter(tmp_path, {"items": [item("a")]})
    first = await collect(db_session, source, run_id=run_id)
    second = await collect(db_session, source, run_id=run_id)

    snapshot_count = db_session.scalar(select(func.count()).select_from(ListingSnapshot))
    assert first.run_id == second.run_id == run_id
    assert snapshot_count == 1


@pytest.mark.asyncio
async def test_out_of_order_observation_does_not_regress_current_projection(
    db_session: Session, tmp_path: Path
) -> None:
    await collect(
        db_session,
        file_adapter(
            tmp_path,
            {"items": [item("a", price_wan=310, observed_at="2026-01-02T00:00:00Z")]},
            name="newer",
        ),
    )
    await collect(
        db_session,
        file_adapter(
            tmp_path,
            {"items": [item("a", price_wan=290, observed_at="2026-01-01T00:00:00Z")]},
            name="older",
        ),
    )

    listing = db_session.scalar(select(Listing).where(Listing.source_listing_id == "a"))
    snapshots = db_session.scalars(
        select(ListingSnapshot).order_by(ListingSnapshot.snapshot_at)
    ).all()
    assert listing is not None and int(listing.total_price) == 3_100_000
    assert [int(snapshot.total_price) for snapshot in snapshots] == [2_900_000, 3_100_000]


class BlockingAdapter:
    source_id = "sample_json"

    def __init__(
        self,
        started: asyncio.Event,
        release: asyncio.Event,
        *,
        scope_key: str = "sample_json:shanghai:blocking",
    ) -> None:
        self.started = started
        self.release = release
        self.scope_key = scope_key

    async def fetch(self) -> FetchResult:
        self.started.set()
        await self.release.wait()
        return FetchResult(
            source_id=self.source_id,
            scope_key=self.scope_key,
            raw_items=[],
            completeness=CrawlCompleteness.COMPLETE,
        )

    def normalize(self, raw_item: Mapping[str, Any]) -> ListingObservation:
        raise AssertionError("empty adapter cannot normalize")

    def source_record_id(self, raw_item: Mapping[str, Any]) -> str | None:
        return None


@pytest.mark.asyncio
async def test_concurrent_same_scope_only_allows_one_worker(postgres_engine: Engine) -> None:
    factory = sessionmaker(postgres_engine, expire_on_commit=False)
    started = asyncio.Event()
    release = asyncio.Event()
    first_adapter = BlockingAdapter(started, release)

    with factory() as first_session, factory() as second_session:
        first_task = asyncio.create_task(CollectorService(first_session).collect(first_adapter))
        await started.wait()
        second_result = await asyncio.wait_for(
            CollectorService(second_session).collect(
                BlockingAdapter(asyncio.Event(), asyncio.Event())
            ),
            timeout=2,
        )
        release.set()
        first_result = await first_task

    assert second_result.status == CrawlRunStatus.ALREADY_RUNNING.value
    assert first_result.status == CrawlRunStatus.SUCCEEDED.value


@pytest.mark.asyncio
async def test_concurrent_different_scopes_run_independently(postgres_engine: Engine) -> None:
    factory = sessionmaker(postgres_engine, expire_on_commit=False)
    started = asyncio.Event()
    release = asyncio.Event()
    second_release = asyncio.Event()
    second_release.set()

    with factory() as first_session, factory() as second_session:
        first_task = asyncio.create_task(
            CollectorService(first_session).collect(
                BlockingAdapter(started, release, scope_key="sample_json:shanghai:xuhui")
            )
        )
        await started.wait()
        second_result = await asyncio.wait_for(
            CollectorService(second_session).collect(
                BlockingAdapter(
                    asyncio.Event(),
                    second_release,
                    scope_key="sample_json:shanghai:putuo",
                )
            ),
            timeout=2,
        )
        release.set()
        first_result = await first_task

    assert first_result.status == CrawlRunStatus.SUCCEEDED.value
    assert second_result.status == CrawlRunStatus.SUCCEEDED.value


class MismatchedAdapter(BlockingAdapter):
    async def fetch(self) -> FetchResult:
        return FetchResult(
            source_id="other_source",
            scope_key=self.scope_key,
            raw_items=[],
            completeness=CrawlCompleteness.COMPLETE,
        )


class AuthBlockedAdapter(BlockingAdapter):
    async def fetch(self) -> FetchResult:
        raise AuthenticationRequiredError("source authentication required: 403")


@pytest.mark.asyncio
async def test_http_retry_creates_one_audit_history(
    db_session: Session,
) -> None:
    attempts = 0
    payload = {
        "schema_version": "1.0",
        "source_id": "sample_json",
        "scope": {"city": "shanghai", "district": "xuhui", "filters": {}},
        "completeness": "complete",
        "coverage": {"reported_total": 1, "items_returned": 1},
        "metadata": {"batch": "retry"},
        "items": [item("retry-a")],
    }

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = ExampleJsonFeedAdapter(
            source_id="sample_json",
            endpoint="https://feed.example.invalid/listings",
            scope=CrawlScope(city="shanghai", district="xuhui"),
            max_attempts=2,
            client=client,
        )
        result = await collect(db_session, source)

    assert attempts == 2
    assert result.completeness == CrawlCompleteness.COMPLETE.value
    assert db_session.scalar(select(func.count()).select_from(CrawlRun)) == 1
    assert db_session.scalar(select(func.count()).select_from(RawSourceRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(ListingSnapshot)) == 1
    assert db_session.scalar(select(func.count()).select_from(ListingEvent)) == 1


@pytest.mark.asyncio
async def test_live_count_drop_becomes_partial_without_reconciliation(
    db_session: Session, tmp_path: Path
) -> None:
    first = file_adapter(
        tmp_path,
        {"items": [item(f"guard-{index}") for index in range(10)]},
        name="guard-first",
    )
    second = file_adapter(
        tmp_path,
        {"items": [item(f"guard-{index}") for index in range(4)]},
        name="guard-second",
    )
    service = CollectorService(
        db_session,
        data_mode=DataMode.LIVE,
        min_complete_count_ratio=0.5,
    )

    first_result = await service.collect(first)
    second_result = await service.collect(second)

    assert first_result.completeness == CrawlCompleteness.COMPLETE.value
    assert second_result.completeness == CrawlCompleteness.PARTIAL.value
    assert second_result.coverage_guard_triggered is True
    assert second_result.coverage_guard_reason == "unexpected_item_count_drop"
    assert second_result.previous_complete_count == 10
    assert second_result.current_count_ratio == 0.4
    assert second_result.reconciliation_performed is False
    assert set(db_session.scalars(select(Listing.status))) == {ListingStatus.ACTIVE.value}


@pytest.mark.asyncio
async def test_partner_csv_without_manifest_never_reconciles_existing_listings(
    db_session: Session, tmp_path: Path
) -> None:
    path = tmp_path / "partner.csv"
    header = "listing_id,url,district,submarket,community,price_wan,area_sqm\n"
    first_rows = (
        "csv-a,https://example.invalid/csv-a,Xuhui,Huajing,A,300,60\n"
        "csv-b,https://example.invalid/csv-b,Xuhui,Huajing,B,310,61\n"
    )
    await asyncio.to_thread(path.write_text, header + first_rows, encoding="utf-8")
    file_sha256 = hashlib.sha256(await asyncio.to_thread(path.read_bytes)).hexdigest()
    manifest_path = Path(f"{path}.manifest.json")
    await asyncio.to_thread(
        manifest_path.write_text,
        json.dumps(
            {
                "manifest_version": "1.0",
                "source_id": "partner_csv",
                "provider": "Test Broker",
                "license_reference": "test-contract",
                "exported_at": "2026-09-04T01:00:00Z",
                "scope": {"city": "shanghai", "filters": {}},
                "completeness": "complete",
                "coverage": {"reported_total": 2, "items_returned": 2},
                "file_name": path.name,
                "file_sha256": file_sha256,
            }
        ),
        encoding="utf-8",
    )
    source = PartnerCsvFeedAdapter(
        source_id="partner_csv",
        endpoint=str(path),
        scope=CrawlScope(city="shanghai"),
        capabilities=ProviderCapabilities(supports_reported_total=True),
    )
    service = CollectorService(db_session, data_mode=DataMode.LIVE)
    first_result = await service.collect(source)

    await asyncio.to_thread(manifest_path.unlink)
    await asyncio.to_thread(
        path.write_text,
        header + "csv-a,https://example.invalid/csv-a,Xuhui,Huajing,A,300,60\n",
        encoding="utf-8",
    )
    second_result = await service.collect(source)

    assert first_result.completeness == CrawlCompleteness.COMPLETE.value
    assert second_result.completeness == CrawlCompleteness.PARTIAL.value
    assert second_result.reconciliation_performed is False
    assert set(db_session.scalars(select(Listing.status))) == {ListingStatus.ACTIVE.value}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_real_gateway_auth_failure_pauses_run_without_retry(
    db_session: Session,
    status: int,
) -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        source = ExampleJsonFeedAdapter(
            source_id="sample_json",
            endpoint="https://feed.example.invalid/listings",
            scope=CrawlScope(city="shanghai", district="xuhui"),
            max_attempts=4,
            client=client,
        )
        with pytest.raises(AuthenticationRequiredError):
            await collect(db_session, source)

    run = db_session.scalar(select(CrawlRun))
    assert attempts == 1
    assert run is not None
    assert run.status == CrawlRunStatus.PAUSED_AUTH.value
    assert run.completeness == CrawlCompleteness.UNKNOWN.value
    assert db_session.scalar(select(func.count()).select_from(RawSourceRecord)) == 0


@pytest.mark.asyncio
async def test_authentication_blocked_run_pauses_without_reconciliation(
    db_session: Session,
) -> None:
    adapter = AuthBlockedAdapter(asyncio.Event(), asyncio.Event())

    with pytest.raises(AuthenticationRequiredError):
        await CollectorService(db_session).collect(adapter)

    run = db_session.scalar(select(CrawlRun))
    assert run is not None
    assert run.status == CrawlRunStatus.PAUSED_AUTH.value
    assert run.completeness == CrawlCompleteness.UNKNOWN.value


@pytest.mark.asyncio
async def test_source_identity_mismatch_fails_run(
    db_session: Session,
) -> None:
    adapter = MismatchedAdapter(asyncio.Event(), asyncio.Event())

    with pytest.raises(SourceIdentityMismatchError):
        await CollectorService(db_session).collect(adapter)

    run = db_session.scalar(select(CrawlRun))
    assert run is not None and run.status == CrawlRunStatus.FAILED.value


@pytest.mark.asyncio
async def test_snapshot_and_listing_history_are_database_protected(
    db_session: Session, postgres_engine: Engine, tmp_path: Path
) -> None:
    await collect(db_session, file_adapter(tmp_path, {"items": [item("a")]}, name="seed"))
    snapshot_id = db_session.scalar(select(ListingSnapshot.id))
    listing_id = db_session.scalar(select(Listing.id))
    raw_record_id = db_session.scalar(select(RawSourceRecord.id))
    assert snapshot_id is not None and listing_id is not None and raw_record_id is not None

    with postgres_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                update(ListingSnapshot)
                .where(ListingSnapshot.id == snapshot_id)
                .values(status="inactive")
            )
        connection.rollback()
        with pytest.raises(DBAPIError):
            connection.execute(delete(ListingSnapshot).where(ListingSnapshot.id == snapshot_id))
        connection.rollback()
        with pytest.raises(DBAPIError):
            connection.execute(
                update(RawSourceRecord)
                .where(RawSourceRecord.id == raw_record_id)
                .values(normalization_error="tampered")
            )
        connection.rollback()
        with pytest.raises(DBAPIError):
            connection.execute(delete(RawSourceRecord).where(RawSourceRecord.id == raw_record_id))
        connection.rollback()
        with pytest.raises(IntegrityError):
            connection.execute(delete(Listing).where(Listing.id == listing_id))
        connection.rollback()

    assert db_session.scalar(select(func.count()).select_from(ListingSnapshot)) == 1
    assert db_session.scalar(select(func.count()).select_from(RawSourceRecord)) == 1
    assert db_session.scalar(select(func.count()).select_from(ListingPresence)) == 1
