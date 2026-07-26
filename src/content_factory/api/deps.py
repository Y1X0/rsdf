"""FastAPI dependency-injection composition root.

This is where concrete providers (LLM, TTS, renderer) get chosen, exactly
once per process, from configuration — see llm/factory.py and
video_production/*/factory.py. Routers and services never import a provider
class directly; they only ever see the interface types, injected here. This
is also the single seam tests override (see tests/conftest.py) to swap in
fakes without touching a single line of business logic.
"""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy.orm import Session

from content_factory.auth.rate_limiter import RateLimiter
from content_factory.auth.rate_limiter_factory import get_auth_rate_limiter as _build_auth_rate_limiter
from content_factory.config import get_settings
from content_factory.db.base import SessionLocal
from content_factory.llm.base import LLMClient
from content_factory.llm.factory import get_llm_client as _build_llm_client
from content_factory.notifications.base import NotificationProvider
from content_factory.notifications.factory import get_notification_provider as _build_notification_provider
from content_factory.services.media_backup import MediaBackupProvider
from content_factory.services.media_backup import get_media_backup_provider as _build_media_backup_provider
from content_factory.video_production.renderer.base import VideoRenderer
from content_factory.video_production.renderer.factory import get_video_renderer as _build_video_renderer
from content_factory.video_production.tts.base import TTSProvider
from content_factory.video_production.tts.factory import get_tts_provider as _build_tts_provider


def get_db() -> Generator[Session, None, None]:
    """Request-scoped session with commit-on-success / rollback-on-error
    semantics — services only ever flush(), the request boundary is what
    actually commits."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@lru_cache
def _llm_client_singleton() -> LLMClient:
    return _build_llm_client(get_settings())


@lru_cache
def _tts_provider_singleton() -> TTSProvider:
    return _build_tts_provider(get_settings())


@lru_cache
def _video_renderer_singleton() -> VideoRenderer:
    return _build_video_renderer(get_settings())


def get_llm_client() -> LLMClient:
    return _llm_client_singleton()


def get_tts_provider() -> TTSProvider:
    return _tts_provider_singleton()


def get_video_renderer() -> VideoRenderer:
    return _video_renderer_singleton()


@lru_cache
def _notification_provider_singleton() -> NotificationProvider:
    return _build_notification_provider(get_settings())


@lru_cache
def _auth_rate_limiter_singleton() -> RateLimiter:
    return _build_auth_rate_limiter(get_settings())


def get_notification_provider() -> NotificationProvider:
    return _notification_provider_singleton()


def get_auth_rate_limiter() -> RateLimiter:
    return _auth_rate_limiter_singleton()


@lru_cache
def _media_backup_provider_singleton() -> MediaBackupProvider:
    return _build_media_backup_provider(get_settings())


def get_media_backup_provider() -> MediaBackupProvider:
    return _media_backup_provider_singleton()
