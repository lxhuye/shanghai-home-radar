from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from home_radar_models.listing import Listing
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from home_radar_api.dependencies import get_db
from home_radar_api.schemas import (
    EventRead,
    ListingDetail,
    ListingHistoryRead,
    ListingPage,
    ListingRead,
    SnapshotRead,
)

router = APIRouter(prefix="/api/v1/listings", tags=["listings"])


@router.get("")
def list_listings(
    db: Annotated[Session, Depends(get_db)],
    district: Annotated[str | None, Query(max_length=80)] = None,
    submarket: Annotated[str | None, Query(max_length=120)] = None,
    status: Annotated[str | None, Query(max_length=32)] = "active",
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListingPage:
    filters = []
    if district:
        filters.append(Listing.district == district)
    if submarket:
        filters.append(Listing.submarket == submarket)
    if status:
        filters.append(Listing.status == status)

    total = db.scalar(select(func.count()).select_from(Listing).where(*filters)) or 0
    listings = db.scalars(
        select(Listing)
        .where(*filters)
        .order_by(Listing.last_seen_at.desc(), Listing.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return ListingPage(
        items=[ListingRead.model_validate(item) for item in listings],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{listing_id}")
def get_listing(listing_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]) -> ListingDetail:
    listing = db.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.snapshots), selectinload(Listing.events))
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    return ListingDetail.model_validate(listing)


@router.get("/{listing_id}/history", response_model=ListingHistoryRead)
def get_listing_history(
    listing_id: uuid.UUID, db: Annotated[Session, Depends(get_db)]
) -> ListingHistoryRead:
    listing = db.scalar(
        select(Listing)
        .where(Listing.id == listing_id)
        .options(selectinload(Listing.snapshots), selectinload(Listing.events))
    )
    if listing is None:
        raise HTTPException(status_code=404, detail="listing not found")
    return ListingHistoryRead(
        listing_id=listing.id,
        snapshots=[SnapshotRead.model_validate(item) for item in listing.snapshots],
        events=[EventRead.model_validate(item) for item in listing.events],
    )
