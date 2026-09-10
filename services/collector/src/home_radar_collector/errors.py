from __future__ import annotations


class CollectorError(Exception):
    """Base class for expected collector failures."""


class SourcePayloadValidationError(CollectorError):
    """The source returned a successful response with an invalid top-level shape."""


class RetryableSourceError(CollectorError):
    """A temporary source response that is safe to retry."""


class AuthenticationRequiredError(CollectorError):
    """The source requires operator action; automatic bypass is forbidden."""


class SourceIdentityMismatchError(CollectorError):
    """Configured, adapter, fetch-result, and observation identities disagree."""


class SourceScopeMismatchError(CollectorError):
    """The provider-declared scope differs from the configured collection scope."""


class ListingNormalizationError(CollectorError):
    """One provider listing cannot be converted to the canonical observation model."""


class CrawlAlreadyRunningError(CollectorError):
    """Another worker owns the source/scope reconciliation lock."""
