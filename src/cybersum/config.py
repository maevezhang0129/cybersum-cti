"""Settings resolution.

One rule holds this module together: ``from_env`` takes the environment as an
argument. Nothing here reads ``os.environ``, and nothing calls ``load_dotenv`` at
import time. Tests construct settings by passing a dict; the three entry points
(Azure Function, CLI, evaluation harness) each hand in the mapping they already
have. The original code resolved configuration at module import in three
different places, which is why it could disagree with itself about which port the
database was on.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Provider = Literal["azure", "openai", "replay"]

PACKAGE_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent.parent
FIXTURES = REPO_ROOT / "fixtures"


def _bool(env: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = env.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _csv(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = env.get(key, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class DatabaseSettings:
    host: str = "localhost"
    port: int = 5432
    name: str = "cybersum"
    user: str = "cybersum"
    password: str = field(default="", repr=False)
    connect_timeout: int = 10

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> DatabaseSettings:
        return cls(
            host=env.get("DB_HOST", "localhost"),
            port=_int(env, "DB_PORT", 5432),
            name=env.get("DB_NAME", "cybersum"),
            user=env.get("DB_USER", "cybersum"),
            password=env.get("DB_PASS", ""),
            connect_timeout=_int(env, "DB_CONNECT_TIMEOUT", 10),
        )

    def connect_kwargs(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "dbname": self.name,
            "user": self.user,
            "password": self.password,
            "connect_timeout": self.connect_timeout,
        }

    def required_keys(self) -> list[str]:
        missing = []
        for key, value in (
            ("DB_HOST", self.host),
            ("DB_NAME", self.name),
            ("DB_USER", self.user),
            ("DB_PASS", self.password),
        ):
            if not value:
                missing.append(key)
        return missing


@dataclass(frozen=True)
class LLMSettings:
    provider: Provider = "replay"
    api_key: str = field(default="", repr=False)
    model: str = "gpt-4o"
    temperature: float = 0.2
    max_retries: int = 3
    # Azure OpenAI only.
    endpoint: str | None = None
    deployment: str | None = None
    api_version: str = "2024-02-15-preview"
    # provider == "replay"
    cassette: Path | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> LLMSettings:
        explicit = (env.get("LLM_PROVIDER") or "").strip().lower()
        if explicit in ("azure", "openai", "replay"):
            provider: Provider = explicit  # type: ignore[assignment]
        elif env.get("OPENAI_API_ENDPOINT") and env.get("OPENAI_DEPLOYMENT_NAME"):
            provider = "azure"
        elif env.get("OPENAI_API_KEY"):
            provider = "openai"
        else:
            # No credentials anywhere: the demo still has to produce a report.
            provider = "replay"

        cassette = env.get("LLM_CASSETTE")
        return cls(
            provider=provider,
            api_key=env.get("OPENAI_API_KEY", ""),
            model=env.get("OPENAI_MODEL", "gpt-4o"),
            temperature=_float(env, "OPENAI_TEMPERATURE", 0.2),
            max_retries=_int(env, "OPENAI_MAX_RETRIES", 3),
            endpoint=env.get("OPENAI_API_ENDPOINT") or None,
            deployment=env.get("OPENAI_DEPLOYMENT_NAME") or None,
            api_version=env.get("OPENAI_API_VERSION", "2024-02-15-preview"),
            cassette=Path(cassette) if cassette else None,
        )

    def required_keys(self) -> list[str]:
        if self.provider == "replay":
            return []
        missing = []
        if not self.api_key:
            missing.append("OPENAI_API_KEY")
        if self.provider == "azure":
            if not self.endpoint:
                missing.append("OPENAI_API_ENDPOINT")
            if not self.deployment:
                missing.append("OPENAI_DEPLOYMENT_NAME")
        return missing


@dataclass(frozen=True)
class EmailSettings:
    """Opt-in. Defaults are inert so no command can accidentally send mail."""

    enabled: bool = False
    smtp_host: str = "localhost"
    smtp_port: int = 25
    sender_name: str = "Cybersum"
    sender_email: str = "cybersum@example.org"
    recipients: tuple[str, ...] = ()
    org_prefix: str = "Cybersum"
    debug_smtp: bool = False

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EmailSettings:
        return cls(
            enabled=_bool(env, "EMAIL_ENABLED", False),
            smtp_host=env.get("SMTP_HOST", "localhost"),
            smtp_port=_int(env, "SMTP_PORT", 25),
            sender_name=env.get("SENDER_NAME", "Cybersum"),
            sender_email=env.get("SENDER_EMAIL", "cybersum@example.org"),
            recipients=_csv(env, "EMAIL_RECIPIENTS"),
            org_prefix=env.get("EMAIL_ORG_PREFIX", "Cybersum"),
            debug_smtp=_bool(env, "DEBUG_SMTP", False),
        )

    def required_keys(self) -> list[str]:
        if not self.enabled:
            return []
        return [] if self.recipients else ["EMAIL_RECIPIENTS"]


@dataclass(frozen=True)
class CollectorSettings:
    """Credentials for the live ingestion collectors.

    Unused by the demo and by the evaluation harness, both of which read
    synthetic data that is already in the database.
    """

    cloudflare_token: str = field(default="", repr=False)
    cloudflare_zones: tuple[tuple[str, str], ...] = ()
    uptimerobot_token: str = field(default="", repr=False)
    azure_subscription_id: str = ""
    azure_resource_group: str = ""
    azure_webapp: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> CollectorSettings:
        zones = []
        for pair in _csv(env, "CLOUDFLARE_ZONES"):
            name, _, zone_id = pair.partition("=")
            if name and zone_id:
                zones.append((name.strip(), zone_id.strip()))
        return cls(
            cloudflare_token=env.get("CLOUDFLARE_API_TOKEN", ""),
            cloudflare_zones=tuple(zones),
            uptimerobot_token=env.get("UPTIMEROBOT_API_TOKEN", ""),
            azure_subscription_id=env.get("AZURE_SUBSCRIPTION_ID", ""),
            azure_resource_group=env.get("AZURE_RESOURCE_GROUP", ""),
            azure_webapp=env.get("AZURE_WEBAPP_NAME", ""),
        )


@dataclass(frozen=True)
class Settings:
    db: DatabaseSettings
    llm: LLMSettings
    email: EmailSettings
    collectors: CollectorSettings
    use_mock_data: bool = False
    debug: bool = False
    mock_data_path: Path = FIXTURES / "aggregated_context.json"
    payload_warn_mb: float = 1.0
    payload_abort_mb: float = 10.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        mock_path = env.get("MOCK_DATA_PATH")
        return cls(
            db=DatabaseSettings.from_env(env),
            llm=LLMSettings.from_env(env),
            email=EmailSettings.from_env(env),
            collectors=CollectorSettings.from_env(env),
            use_mock_data=_bool(env, "USE_MOCK_DATA", False),
            debug=_bool(env, "DEBUG", False) or _bool(env, "DEBUG_MODE", False),
            mock_data_path=Path(mock_path) if mock_path else FIXTURES / "aggregated_context.json",
            payload_warn_mb=_float(env, "PAYLOAD_WARN_MB", 1.0),
            payload_abort_mb=_float(env, "PAYLOAD_ABORT_MB", 10.0),
        )

    def missing_required(self, *, need_db: bool, need_llm: bool = True) -> list[str]:
        """Env var names that must be set for this run, in declaration order."""
        missing: list[str] = []
        if need_llm:
            missing += self.llm.required_keys()
        if need_db:
            missing += self.db.required_keys()
        missing += self.email.required_keys()
        return missing
