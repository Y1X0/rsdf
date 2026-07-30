"""Default publishing backend: no external dependency, always available —
the NullRenderer/SilentTTSProvider pattern applied to publishing. Doesn't
actually post anywhere; it hands the request off as "ready to publish
manually" (services/publishing_service.py records this as a SCHEDULED
Publication, not PUBLISHED, since a human still has to actually post it).
"""

from content_factory.logging_config import get_logger
from content_factory.publishing.base import PublishingProvider, PublishRequest, PublishResult

logger = get_logger(__name__)


class ManualPublishingProvider(PublishingProvider):
    def publish(self, request: PublishRequest) -> PublishResult:
        logger.info(
            "manual_publish_ready",
            video_id=request.video_id,
            title=request.title,
            contains_ai_voice=request.contains_ai_voice,
            contains_ai_visual=request.contains_ai_visual,
        )
        return PublishResult(provider="manual", published=False, external_post_id=None)
