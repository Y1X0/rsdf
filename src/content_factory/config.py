"""Application configuration, loaded entirely from environment variables.

No secret ever has a hardcoded default here — API keys default to an empty
string, and every provider factory (llm/factory.py, video_production/*/factory.py)
treats "no key configured" as an explicit, supported state (falling back to a
fake/silent/null provider) rather than raising at import time. This lets the
whole application and test suite run with zero real credentials.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"

    database_url: str = "sqlite:///./var/content_factory.db"

    llm_provider: str = "anthropic"  # "anthropic" | "groq" | "fake"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_model_version: str = "2026-02-01"

    # --- Groq (free-tier LLM provider, added for the pilot per the
    # Anthropic-billing blocker) — an alternative real provider, not a
    # replacement; see llm/providers/groq_provider.py. OpenAI-compatible
    # REST API, so no new SDK dependency (lazy httpx import, same pattern
    # as every other real provider in this codebase).
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    tts_provider: str = "silent"  # "elevenlabs" | "silent"
    elevenlabs_api_key: str = ""

    renderer_backend: str = "null"  # "template_pillow" | "null"
    media_storage_dir: str = "./var/media"

    # --- Clip factory (long-form video -> short-clip montage) ---
    # Transcription: "null" (default) returns an empty transcript with no
    # external call; "groq" uses Groq's Whisper endpoint (reuses the same
    # GROQ_API_KEY as the LLM provider above).
    transcription_provider: str = "null"  # "groq" | "null"
    groq_whisper_model: str = "whisper-large-v3"
    # Clip rendering: "null" (default) writes a manifest referencing the cut
    # range instead of a real video file, so the pipeline works without
    # ffmpeg installed; "ffmpeg" actually cuts the source video (needs the
    # "rendering" extra: pip install '.[rendering]', same imageio-ffmpeg
    # bundled binary TemplatePillowRenderer already uses).
    clip_renderer_backend: str = "null"  # "ffmpeg" | "null"
    # Speaker diarization ("who is talking, and when"): "null" (default)
    # assumes a single speaker for the whole clip - zero dependencies, and
    # the correct choice for this app's current free-tier hosting reality
    # (see diarization/providers/pyannote_provider.py's docstring for the
    # measured reasoning). "pyannote" is a real, much heavier provider -
    # meant for a deployment with real memory/CPU headroom, opted into
    # explicitly, never a default anyone gets by accident.
    diarization_provider: str = "null"  # "pyannote" | "null"
    huggingface_token: str = ""

    # --- Content Rewards connector (content_sources/) ---
    # "manual" (default): no external video source, sourcing stays exactly
    # as manual as it is today (POST /source-videos upload). "content_rewards"
    # selects ContentRewardsProvider - currently a placeholder returning
    # synthetic test videos (Milestone 1); real contentrewards.com requests
    # are wired in a later milestone once captured from the browser.
    content_source_provider: str = "manual"  # "content_rewards" | "manual"

    # --- Auth (v1.1, PHASE1_AUDIT.md F2) ---
    # No user database in Phase 1 — a small, fixed set of pre-shared service
    # credentials is issued JWTs, matching the actual single-operator usage
    # today. jwt_secret_key has no safe default: if it's empty, every
    # authenticated route fails closed (500, "not configured") rather than
    # silently accepting unsigned/unverifiable tokens.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    jwt_expires_minutes: int = 60
    auth_client_id: str = "phase1-operator"
    auth_client_secret: str = ""

    # --- Auth token rate limiting (Phase 2 M1, PHASE1_AUDIT_v2.md N1) ---
    # A fixed-window limiter, in-process (no new infra) — see
    # api/routers/auth.py. Deliberately generous defaults since this guards
    # against brute-forcing AUTH_CLIENT_SECRET, not normal usage.
    auth_token_rate_limit_max_attempts: int = 10
    auth_token_rate_limit_window_seconds: int = 60

    # --- Notifications (Phase 2 M1) ---
    notification_provider: str = "log"  # "log" | "slack" | "email"
    slack_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_from_addr: str = ""
    smtp_to_addr: str = ""

    # --- Quality gating (Phase 2 M2) ---
    # Opt-in, disabled by default: originality_score/policy_risk_score both
    # range 0..100, so a floor of 0 (score can never be < 0) and a ceiling
    # of 100 (score can never be > 100) mean neither threshold can ever
    # trigger until an operator deliberately tightens them — preserving
    # Phase 1's "quality scores are informational only" behavior exactly.
    quality_originality_auto_reject_floor: float = 0.0
    quality_policy_risk_auto_reject_ceiling: float = 100.0

    # --- Creator Account Management (Phase 2 M3) ---
    # No safe default — an OAuth token can only ever be encrypted/decrypted
    # if this is set; see services/token_encryption.py. Generate with:
    # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""
    # Warmup graduation criterion (ARCHITECTURE.md §8c). "Minimum organic
    # post count" is the other stated criterion but has no data source yet
    # (that's Publishing Agent/M4's `publications` table) — documented gap,
    # not a silent omission.
    account_warmup_minimum_age_days: int = 7

    # --- Publishing Agent (Phase 2 M4) ---
    # Hard kill-switch, independent of any per-account state — flip to
    # false to hard-disable POST /videos/{id}/publish for emergency
    # rollback, regardless of account health or credentials configured.
    publishing_enabled: bool = True
    # Each is empty by default; publishing/factory.py falls back to
    # ManualPublishingProvider whenever a platform's credentials (or a
    # given account's own decrypted access token) aren't present.
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    youtube_client_id: str = ""
    youtube_client_secret: str = ""
    instagram_app_id: str = ""
    instagram_app_secret: str = ""

    # --- Media backup / public asset hosting (Production Hardening Sprint
    # H3, DR4; extended to close the profit loop's public-URL blocker) ---
    # Off by default: local disk (media_storage_dir) is always the primary
    # read path regardless of this setting — see services/media_backup.py's
    # own docstring. AWS/R2 credentials come from boto3's own standard
    # environment variables (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/
    # AWS_REGION), not custom settings here.
    media_backup_enabled: bool = False
    # Leave empty to skip S3 entirely and use the zero-cost option instead:
    # this app's own public `/public-media` route (api/main.py) serving
    # media_storage_dir directly — set media_backup_public_base_url to this
    # service's own https:// base URL + "/public-media" and no cloud
    # storage account is needed at all (see
    # services/media_backup.py::LocalDiskMediaBackupProvider). Only set
    # this when you actually want S3/R2 instead.
    media_backup_s3_bucket: str = ""
    media_backup_s3_prefix: str = "content-factory/media"
    # Only needed for an S3-compatible service other than real AWS S3 (e.g.
    # Cloudflare R2's account-specific endpoint). Leave empty for real AWS.
    media_backup_s3_endpoint_url: str = ""
    # The real, already-verified-reachable base URL assets are servable
    # from — either your S3/R2 bucket's public URL (with media_backup_s3_bucket
    # set), or this same app's own base URL + "/public-media" (with
    # media_backup_s3_bucket left empty) — WITHOUT a trailing slash. Required
    # for publishing_service.py to ever consider an uploaded asset
    # publishable; empty means uploads still happen (durability backup) but
    # nothing can ever be auto-published.
    media_backup_public_base_url: str = ""

    # --- Distributed rate limiting (Production Hardening Sprint H4, S2/SC1) ---
    # "memory" (default) preserves Phase 1/2's exact existing behavior —
    # correct for a single worker process, silently *not* shared across
    # more than one. "redis" is required the moment WEB_CONCURRENCY (or
    # replica count) is raised above 1 — see docs/DEPLOYMENT.md §6.
    rate_limit_backend: str = "memory"  # "memory" | "redis"
    redis_url: str = ""

    # --- Observability (Production Hardening Sprint H6) ---
    # Metrics are on by default (no secret involved, standard ops
    # practice) but only actually mounted if the `observability` extra is
    # installed — see api/main.py. Error tracking is opt-in: sentry_dsn
    # has no safe default, so Sentry stays fully inactive (and
    # sentry_sdk is never even imported) until an operator sets it.
    metrics_enabled: bool = True
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0

    def resolved_llm_provider(self) -> str:
        """Fall back to the fake provider if no key is configured, regardless
        of what LLM_PROVIDER says — prevents the app crashing on missing
        secrets and makes "no key" a first-class, testable state."""
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            return "fake"
        if self.llm_provider == "groq" and not self.groq_api_key:
            return "fake"
        return self.llm_provider

    def resolved_tts_provider(self) -> str:
        if self.tts_provider == "elevenlabs" and not self.elevenlabs_api_key:
            return "silent"
        return self.tts_provider

    def resolved_transcription_provider(self) -> str:
        if self.transcription_provider == "groq" and not self.groq_api_key:
            return "null"
        return self.transcription_provider

    def resolved_diarization_provider(self) -> str:
        if self.diarization_provider == "pyannote" and not self.huggingface_token:
            return "null"
        return self.diarization_provider

    def resolved_content_source_provider(self) -> str:
        """No credential-based fallback yet (Milestone 1's ContentRewardsProvider
        is a placeholder needing no real secret) - kept as a resolved_*
        method regardless, matching every other provider toggle's shape, so
        a later milestone's real credential check slots in here without
        callers changing."""
        return self.content_source_provider

    def resolved_notification_provider(self) -> str:
        if self.notification_provider == "slack" and not self.slack_webhook_url:
            return "log"
        if self.notification_provider == "email" and not (self.smtp_host and self.smtp_to_addr):
            return "log"
        return self.notification_provider

    def media_storage_path(self) -> Path:
        path = Path(self.media_storage_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def validate_production_safety(self) -> None:
        """Production Hardening Sprint H1 — closes a real, previously-silent
        gap: `environment` existed as a field but nothing ever read it, so
        an operator who forgot to set `DATABASE_URL` in production got a
        fully-working app quietly running on local SQLite (data loss on the
        next container restart/redeploy) with no error, no warning, nothing.
        Called once at app startup (see api/main.py's `create_app`), never
        on the request path — this is a boot-time check, not a per-request
        one, and deliberately does nothing at all unless
        `ENVIRONMENT=production` is explicitly set, so every existing
        dev/test code path (`environment` defaults to `"development"`) is
        completely unaffected.
        """
        if self.environment != "production":
            return

        problems = []
        if self.database_url.startswith("sqlite"):
            problems.append(
                "DATABASE_URL points at SQLite while ENVIRONMENT=production. "
                "SQLite is a supported fallback for local dev/tests only — a real "
                "deployment needs a real Postgres instance with backups (see "
                "docs/DATABASE_OPERATIONS.md)."
            )
        if not self.jwt_secret_key:
            problems.append("JWT_SECRET_KEY is unset while ENVIRONMENT=production.")
        elif len(self.jwt_secret_key) < 32:
            problems.append(
                f"JWT_SECRET_KEY is only {len(self.jwt_secret_key)} characters while "
                "ENVIRONMENT=production; use at least 32 (e.g. "
                "`python -c \"import secrets; print(secrets.token_urlsafe(48))\"`)."
            )

        if problems:
            raise RuntimeError(
                "Refusing to start with ENVIRONMENT=production: " + " ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
