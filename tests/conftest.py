"""Shared test fixtures.

Every fixture here avoids real external services entirely: the DB is
in-memory SQLite (see db/base.py's docstring on why the schema is portable),
and the LLM/TTS/renderer fakes are the same ones config.py falls back to
automatically when no real credentials are configured — so the test suite
exercises the exact "no secrets present" code path the app itself uses.
"""

import json
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import content_factory.db.models  # noqa: F401 - registers all tables on Base.metadata
from content_factory.db.base import Base
from content_factory.llm.providers.fake_provider import FakeLLMClient
from content_factory.video_production.renderer.providers.null_renderer import NullRenderer
from content_factory.video_production.tts.providers.silent_provider import SilentTTSProvider


def _make_sqlite_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session():
    engine = _make_sqlite_engine()
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def canned_llm_response(system: str, prompt: str) -> str:
    """A single, reasonably realistic canned response used by the API test
    suite's default fake LLM — deterministic, but shaped like real output so
    downstream parsing/storage logic is genuinely exercised."""
    if "json array" in prompt.lower():
        return json.dumps(
            [
                {
                    "variant_label": "variant_1",
                    "hook_text": "You won't believe this trick",
                    "full_text": "You won't believe this trick. Here's how it works. Try it today.",
                    "cta_text": "Follow for more",
                    "target_duration_s": 20,
                },
                {
                    "variant_label": "variant_2",
                    "hook_text": "Stop scrolling if you want to save money",
                    "full_text": "Stop scrolling if you want to save money. Here's the method. Comment below.",
                    "cta_text": "Comment your results",
                    "target_duration_s": 25,
                },
            ]
        )
    return json.dumps(
        {
            "brief_text": "This niche responds well to countdown-style hooks.",
            "niche_insights": "Short punchy hooks outperform long intros.",
            "competitor_patterns": [
                {
                    "pattern_type": "hook",
                    "description": "Countdown format with numbers in the first 2 seconds",
                }
            ],
            "competitor_hooks": [
                {
                    "hook_text": "3 things nobody tells you about saving money",
                    "hook_type": "countdown",
                }
            ],
            "recommended_angles": ["Budgeting myths", "Quick win tips"],
        }
    )


@pytest.fixture()
def fake_llm_client() -> FakeLLMClient:
    return FakeLLMClient(response_builder=canned_llm_response)


@pytest.fixture()
def tmp_media_dir(tmp_path) -> Path:
    return tmp_path / "media"


@pytest.fixture()
def silent_tts_provider(tmp_media_dir) -> SilentTTSProvider:
    return SilentTTSProvider(storage_dir=tmp_media_dir / "audio")


@pytest.fixture()
def null_video_renderer(tmp_media_dir) -> NullRenderer:
    return NullRenderer(storage_dir=tmp_media_dir / "video")


@pytest.fixture()
def client(fake_llm_client, silent_tts_provider, null_video_renderer):
    """A FastAPI TestClient wired to an isolated in-memory DB and the fakes
    above — no network access, no real API keys, fully deterministic."""
    from fastapi.testclient import TestClient

    from content_factory.api import deps
    from content_factory.api.main import app

    engine = _make_sqlite_engine()
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    def override_get_db():
        db = session_factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[deps.get_db] = override_get_db
    app.dependency_overrides[deps.get_llm_client] = lambda: fake_llm_client
    app.dependency_overrides[deps.get_tts_provider] = lambda: silent_tts_provider
    app.dependency_overrides[deps.get_video_renderer] = lambda: null_video_renderer

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    engine.dispose()
