from pydantic import BaseModel


class DashboardSummaryOut(BaseModel):
    campaign_count: int
    video_counts_by_status: dict[str, int]
    pending_review_count: int
    total_cost_usd: float
    total_revenue_usd: float
    profit_usd: float


class SettingsStatusOut(BaseModel):
    """Read-only, non-secret provider/environment status for the Settings
    page — mode selectors only (e.g. "groq"), never a key or credential
    value.

    `*_configured` mirrors the raw setting (what an operator set
    LLM_PROVIDER/etc to); `*_effective` mirrors what's actually being used
    at runtime, i.e. `Settings.resolved_*_provider()` — which silently
    falls back to a safe default (e.g. "fake") when the matching API key
    is missing (config.py's own documented behavior). Showing only the
    configured value here previously made this page actively misleading:
    it could read "groq" while the app was really running on the fake,
    zero-content provider because GROQ_API_KEY was unset. `*_using_fallback`
    makes that gap impossible to miss instead of requiring the reader to
    notice the two values differ."""

    environment: str
    llm_provider_configured: str
    llm_provider_effective: str
    llm_provider_using_fallback: bool
    tts_provider_configured: str
    tts_provider_effective: str
    tts_provider_using_fallback: bool
    transcription_provider_configured: str
    transcription_provider_effective: str
    transcription_provider_using_fallback: bool
    notification_provider_configured: str
    notification_provider_effective: str
    notification_provider_using_fallback: bool
    renderer_backend: str
    clip_renderer_backend: str
    publishing_enabled: bool
    media_backup_enabled: bool
    rate_limit_backend: str
