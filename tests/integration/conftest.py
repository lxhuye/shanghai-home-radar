from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    database_url = os.getenv("SHR_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("SHR_TEST_DATABASE_URL is required for PostgreSQL integration tests")
    if not (make_url(database_url).database or "").endswith("_test"):
        raise RuntimeError("integration tests refuse to use a database without a _test suffix")

    alembic_config = Config("alembic.ini")
    alembic_config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(postgres_engine: Engine) -> Iterator[Session]:
    with postgres_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE TABLE decision_validation_item, decision_validation_batch, "
                "decision_assessment, future_assessment, future_factor_observation, "
                "future_project, employment_center, valuation_result, market_baseline, "
                "baseline_materialization_run, "
                "market_observation, listing_presence, raw_source_record, listing_event, "
                "listing_snapshot, inquiry, community, listing, crawl_run "
                "RESTART IDENTITY CASCADE"
            )
        )
    factory = sessionmaker(postgres_engine, expire_on_commit=False)
    with factory() as session:
        yield session
        session.rollback()
