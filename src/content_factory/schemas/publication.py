from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from content_factory.db.models.enums import AccountPlatform, PublicationStatus


class PublishRequestBody(BaseModel):
    account_id: int
    title: str = Field(max_length=300)
    description: str = Field(default="", max_length=2_000)
    hashtags: list[str] = Field(default_factory=list, max_length=30)
    scheduled_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=200)


class PublicationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    video_id: int
    campaign_id: int | None
    account_id: int
    platform: AccountPlatform
    external_post_id: str | None
    title: str
    description: str
    hashtags: list[str]
    scheduled_at: datetime
    published_at: datetime | None
    status: PublicationStatus
    created_at: datetime
