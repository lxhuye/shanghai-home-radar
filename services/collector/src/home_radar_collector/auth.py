from __future__ import annotations

import re
from base64 import b64encode
from enum import StrEnum
from urllib.parse import parse_qsl, urlsplit

import httpx
from home_radar_shared.config import Settings
from pydantic import BaseModel, ConfigDict, SecretStr, model_validator

from home_radar_collector.feed import _contains_sensitive_key

HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
FORBIDDEN_CUSTOM_HEADERS = {
    "authorization",
    "connection",
    "content-length",
    "cookie",
    "host",
    "proxy-authorization",
    "set-cookie",
    "transfer-encoding",
}


class ProviderAuthMode(StrEnum):
    NONE = "NONE"
    BEARER_TOKEN = "BEARER_TOKEN"
    BASIC_AUTH = "BASIC_AUTH"
    CUSTOM_HEADER = "CUSTOM_HEADER"


class ProviderAuth(BaseModel):
    """Validated provider credentials that stay secret until request construction."""

    model_config = ConfigDict(frozen=True)

    mode: ProviderAuthMode = ProviderAuthMode.NONE
    bearer_token: SecretStr | None = None
    basic_username: SecretStr | None = None
    basic_password: SecretStr | None = None
    custom_header_name: str | None = None
    custom_header_value: SecretStr | None = None

    @model_validator(mode="after")
    def validate_credentials(self) -> ProviderAuth:
        configured = {
            "bearer_token": self.bearer_token,
            "basic_username": self.basic_username,
            "basic_password": self.basic_password,
            "custom_header_name": self.custom_header_name,
            "custom_header_value": self.custom_header_value,
        }
        allowed_by_mode = {
            ProviderAuthMode.NONE: set(),
            ProviderAuthMode.BEARER_TOKEN: {"bearer_token"},
            ProviderAuthMode.BASIC_AUTH: {"basic_username", "basic_password"},
            ProviderAuthMode.CUSTOM_HEADER: {"custom_header_name", "custom_header_value"},
        }
        allowed = allowed_by_mode[self.mode]
        unexpected = [
            name for name, value in configured.items() if value is not None and name not in allowed
        ]
        if unexpected:
            raise ValueError("provider credentials conflict with configured auth mode")

        missing = [name for name in allowed if configured[name] is None]
        if missing:
            raise ValueError("provider auth mode is missing required credentials")

        secret_values = [
            value.get_secret_value()
            for value in (
                self.bearer_token,
                self.basic_username,
                self.basic_password,
                self.custom_header_value,
            )
            if value is not None
        ]
        if any(not value.strip() or "\r" in value or "\n" in value for value in secret_values):
            raise ValueError("provider credentials contain an invalid value")

        if self.basic_username is not None and ":" in self.basic_username.get_secret_value():
            raise ValueError("basic auth username must not contain a colon")

        if self.custom_header_name is not None:
            normalized = self.custom_header_name.strip().lower()
            if not HEADER_NAME_PATTERN.fullmatch(self.custom_header_name):
                raise ValueError("custom auth header name is invalid")
            if normalized in FORBIDDEN_CUSTOM_HEADERS:
                raise ValueError("custom auth header name is forbidden")
        return self

    @property
    def enabled(self) -> bool:
        return self.mode is not ProviderAuthMode.NONE

    def request_headers(self) -> dict[str, str]:
        if self.mode is ProviderAuthMode.BEARER_TOKEN:
            assert self.bearer_token is not None
            return {"Authorization": f"Bearer {self.bearer_token.get_secret_value()}"}
        if self.mode is ProviderAuthMode.CUSTOM_HEADER:
            assert self.custom_header_name is not None
            assert self.custom_header_value is not None
            return {
                self.custom_header_name: self.custom_header_value.get_secret_value(),
            }
        return {}

    def request_auth(self) -> httpx.BasicAuth | None:
        if self.mode is not ProviderAuthMode.BASIC_AUTH:
            return None
        assert self.basic_username is not None
        assert self.basic_password is not None
        return httpx.BasicAuth(
            self.basic_username.get_secret_value(),
            self.basic_password.get_secret_value(),
        )

    def reject_reflected_credentials(self, value: object) -> None:
        secrets: list[str] = []
        if self.bearer_token is not None:
            token = self.bearer_token.get_secret_value()
            secrets.extend((token, f"Bearer {token}"))
        if self.custom_header_value is not None:
            secrets.append(self.custom_header_value.get_secret_value())
        if self.basic_username is not None and self.basic_password is not None:
            pair = (
                f"{self.basic_username.get_secret_value()}:{self.basic_password.get_secret_value()}"
            )
            encoded = b64encode(pair.encode()).decode()
            secrets.extend(
                (self.basic_password.get_secret_value(), pair, encoded, f"Basic {encoded}")
            )

        def inspect(nested: object) -> None:
            if isinstance(nested, dict):
                for item in nested.values():
                    inspect(item)
            elif isinstance(nested, list):
                for item in nested:
                    inspect(item)
            elif isinstance(nested, str) and any(secret in nested for secret in secrets):
                raise ValueError("provider response reflected authentication material")

        inspect(value)


def build_provider_auth(settings: Settings) -> ProviderAuth:
    def configured_secret(value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value():
            return None
        return value

    try:
        mode = ProviderAuthMode(settings.collector_auth_mode.strip().upper())
    except ValueError as exc:
        raise ValueError("unsupported provider auth mode") from exc
    return ProviderAuth(
        mode=mode,
        bearer_token=configured_secret(settings.collector_bearer_token),
        basic_username=configured_secret(settings.collector_basic_username),
        basic_password=configured_secret(settings.collector_basic_password),
        custom_header_name=settings.collector_custom_header_name or None,
        custom_header_value=configured_secret(settings.collector_custom_header_value),
    )


def validate_provider_endpoint(
    endpoint: str,
    *,
    auth: ProviderAuth,
    environment: str,
) -> None:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"}:
        if auth.enabled:
            raise ValueError("provider authentication requires an HTTP endpoint")
        return
    if parsed.username or parsed.password:
        raise ValueError("provider endpoint must not contain credentials")
    if any(_contains_sensitive_key(key) for key, _ in parse_qsl(parsed.query)):
        raise ValueError("provider endpoint must not contain credential query parameters")
    if environment.strip().lower() not in {"development", "test"} and parsed.scheme != "https":
        raise ValueError("provider endpoint must use HTTPS outside development and test")


def provider_origins_match(first: str, second: str) -> bool:
    def origin(value: str) -> tuple[str, str | None, int | None]:
        parsed = urlsplit(value)
        default_port = 443 if parsed.scheme == "https" else 80 if parsed.scheme == "http" else None
        return parsed.scheme.lower(), parsed.hostname, parsed.port or default_port

    return origin(first) == origin(second)
