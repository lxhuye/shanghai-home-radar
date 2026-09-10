from __future__ import annotations

import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Header, HTTPException, status
from home_radar_shared.config import get_settings
from home_radar_shared.database import get_session_factory
from sqlalchemy.orm import Session


def get_db() -> Iterator[Session]:
    with get_session_factory()() as session:
        yield session


def require_api_key(
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected = get_settings().api_key
    if api_key is None or not secrets.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="valid private API key required",
        )
