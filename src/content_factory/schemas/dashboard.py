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
    value. Every field mirrors an existing Settings attribute as-is."""

    environment: str
    llm_provider: str
    tts_provider: str
    renderer_backend: str
    clip_renderer_backend: str
    transcription_provider: str
    notification_provider: str
    publishing_enabled: bool
    media_backup_enabled: bool
    rate_limit_backend: str
