from __future__ import annotations

from enum import StrEnum


class ListingStatus(StrEnum):
    ACTIVE = "active"
    MISSING_CANDIDATE = "missing_candidate"
    INACTIVE = "inactive"
    SOLD = "sold"

    # Compatibility aliases for the P1 collector while callers migrate.
    TEMPORARILY_UNAVAILABLE = MISSING_CANDIDATE
    REMOVED = INACTIVE


class ListingEventType(StrEnum):
    NEW = "new"
    PRICE_CUT = "price_cut"
    PRICE_INCREASE = "price_increase"
    MISSING_CANDIDATE = "missing_candidate"
    INACTIVATED = "inactivated"
    RELISTED = "relisted"

    TEMPORARY_DISAPPEARANCE = MISSING_CANDIDATE
    FINAL_DISAPPEARANCE = INACTIVATED


class CrawlRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PAUSED_AUTH = "paused_auth"
    ALREADY_RUNNING = "already_running"


class CrawlCompleteness(StrEnum):
    UNKNOWN = "unknown"
    COMPLETE = "complete"
    PARTIAL = "partial"

    INCOMPLETE = PARTIAL


class NormalizationStatus(StrEnum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"

    NORMALIZED = SUCCESS
    PARSE_ERROR = FAILED


class MarketObservationType(StrEnum):
    LISTING = "listing"
    TRANSACTION = "transaction"
    RENTAL = "rental"
    OFFICIAL_INDEX = "official_index"
    EXTERNAL_BASELINE = "external_baseline"


class BaselineLevel(StrEnum):
    SHANGHAI = "shanghai"
    DISTRICT = "district"
    SUBMARKET = "submarket"
    COMMUNITY = "community"


class BaselineConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class DataMode(StrEnum):
    DEMO = "demo"
    SAMPLE = "sample"
    LIVE = "live"


class ValuationBasis(StrEnum):
    LISTING_DERIVED = "listing_derived"
    TRANSACTION_SUPPORTED = "transaction_supported"
    MIXED_SOURCE = "mixed_source"


class TransactionSupport(StrEnum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"


class ComparableTier(StrEnum):
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
    TIER_4 = "tier_4"


class ValuationDecision(StrEnum):
    PASS = "pass"
    WATCH = "watch"
    CONTACT = "contact"
    VIEW = "view"
    ATTACK = "attack"


class FutureFactorType(StrEnum):
    EMPLOYMENT_ACCESSIBILITY = "employment_accessibility"
    TRANSPORT_ACCESSIBILITY = "transport_accessibility"
    SUPPLY_SCARCITY = "supply_scarcity"
    BUYER_POOL_DEPTH = "buyer_pool_depth"
    COMMUNITY_COMPETITIVENESS = "community_competitiveness"
    URBAN_RENEWAL = "urban_renewal"
    RENTAL_DEMAND = "rental_demand"
    PUBLIC_SERVICES = "public_services"
    PLANNING_REALIZATION = "planning_realization"
    MARKET_CYCLE = "market_cycle"
    BUILDING_AGING = "building_aging"
    PRODUCT_OBSOLESCENCE = "product_obsolescence"


class FutureProjectStatus(StrEnum):
    CURRENT = "current"
    UNDER_CONSTRUCTION = "under_construction"
    APPROVED = "approved"
    PLANNED = "planned"
    CONCEPTUAL = "conceptual"


class CalibrationState(StrEnum):
    CALIBRATED = "calibrated"
    PARTIALLY_CALIBRATED = "partially_calibrated"
    UNCALIBRATED = "uncalibrated"


class PresenceState(StrEnum):
    ACTIVE = "active"
    MISSING_CANDIDATE = "missing_candidate"
    INACTIVE = "inactive"


# Compatibility alias for the P1 collector while callers migrate.
CollectionRunStatus = CrawlRunStatus
