from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol

from home_radar_forecasting.domain import (
    EmploymentCenterInput,
    FactorEvidence,
    FutureInputs,
    FutureProjectInput,
)


class EmploymentDataSource(Protocol):
    def employment_centers(
        self, *, data_mode: str, as_of: datetime
    ) -> tuple[EmploymentCenterInput, ...]: ...


class TransportDataSource(Protocol):
    def transport_projects(
        self, *, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> tuple[FutureProjectInput, ...]: ...


class SupplyDataSource(Protocol):
    def supply_evidence(
        self, *, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> FactorEvidence | None: ...


class UrbanRenewalDataSource(Protocol):
    def renewal_evidence(
        self, *, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> tuple[FutureProjectInput, ...]: ...


class RentalBaselineSource(Protocol):
    def rental_evidence(
        self, *, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> FactorEvidence | None: ...


class PublicServicesDataSource(Protocol):
    def public_services_evidence(
        self, *, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> FactorEvidence | None: ...


class BuyerPoolDataSource(Protocol):
    def buyer_pool_evidence(
        self, *, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> FactorEvidence | None: ...


class MarketCycleDataSource(Protocol):
    def market_cycle_evidence(
        self, *, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> FactorEvidence | None: ...


class MacroFinancingDataSource(Protocol):
    def macro_financing_evidence(
        self, *, data_mode: str, as_of: datetime
    ) -> FactorEvidence | None: ...


class FutureInputRepository(Protocol):
    def load_inputs(
        self, listing_id: uuid.UUID, data_mode: str, as_of: datetime
    ) -> FutureInputs: ...
