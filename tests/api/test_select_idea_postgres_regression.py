"""Regression test for a real, reproduced production incident on
`POST /ideas/{id}/select`: a real LLM response with a variant_label longer
than the `scripts.variant_label` column's VARCHAR(50) bound raised a
Postgres DataError at db.flush() inside ScriptAgent, which left the
request's SQLAlchemy session in a "pending rollback" state - and the
router's own exception handler for that failure then crashed *again*
trying to read from that poisoned session, producing a bare 500 with none
of the intended diagnostic detail.

SQLite (used by every other test in this suite via `client`/`db_session`)
does not enforce VARCHAR length bounds at all, so this can only be
reproduced and proven fixed against a real Postgres instance - same
rationale, and same "skip cleanly if unavailable" pattern, as
test_budget_governor_concurrency.py.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from content_factory.agents.script_agent import _VARIANT_LABEL_MAX_LENGTH
from content_factory.auth.rate_limiter import FixedWindowRateLimiter
from content_factory.llm.providers.fake_provider import FakeLLMClient
from content_factory.notifications.base import NotificationProvider, NotificationResult
from content_factory.video_production.renderer.providers.null_renderer import NullRenderer
from content_factory.video_production.tts.providers.silent_provider import SilentTTSProvider

REAL_POSTGRES_URL = "postgresql+psycopg2://content_factory:content_factory@localhost:5432/content_factory"


def _real_postgres_available() -> bool:
    try:
        engine = create_engine(REAL_POSTGRES_URL)
        with engine.connect():
            pass
        engine.dispose()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _real_postgres_available(), reason="no local Postgres instance available")


class _SilentNotificationProvider(NotificationProvider):
    def send(self, request):
        return NotificationResult(channel="log", delivered=True)


LONG_LABEL = "Countdown Hook Variant Focusing on Urgency, FOMO, and a Direct Call To Action for Maximum Watch Time"


def _canned_long_label_response(system: str, prompt: str) -> str:
    if "json array" not in prompt.lower():
        return "{}"
    return json.dumps(
        [{"variant_label": LONG_LABEL, "hook_text": "a real hook", "full_text": "a real full script body"}]
    )


@pytest.fixture
def pg_client(tmp_path):
    from fastapi.testclient import TestClient

    from content_factory.api import deps
    from content_factory.api.main import app

    engine = create_engine(REAL_POSTGRES_URL)
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
    app.dependency_overrides[deps.get_llm_client] = lambda: FakeLLMClient(
        response_builder=_canned_long_label_response
    )
    app.dependency_overrides[deps.get_tts_provider] = lambda: SilentTTSProvider(storage_dir=tmp_path / "audio")
    app.dependency_overrides[deps.get_video_renderer] = lambda: NullRenderer(storage_dir=tmp_path / "video")
    app.dependency_overrides[deps.get_notification_provider] = lambda: _SilentNotificationProvider()
    app.dependency_overrides[deps.get_auth_rate_limiter] = lambda: FixedWindowRateLimiter(
        max_attempts=10_000, window_seconds=60
    )

    client = TestClient(app, raise_server_exceptions=False)
    token = client.post(
        "/auth/token", json={"client_id": "test-operator", "client_secret": "test-operator-secret"}
    ).json()["access_token"]
    client.headers.update({"Authorization": f"Bearer {token}"})

    yield client

    app.dependency_overrides.clear()
    engine.dispose()


def test_select_idea_survives_a_real_llm_response_with_an_overlong_variant_label(pg_client):
    campaign = pg_client.post(
        "/campaigns", json={"brand_name": "PG Regression Co", "niche_name": "personal_finance", "cpm_rate": 4.0}
    ).json()
    idea = pg_client.post(
        f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "long label regression"}
    ).json()

    resp = pg_client.post(f"/ideas/{idea['id']}/select", json={})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["idea"]["status"] == "selected"
    assert body["stage_reached"] == "rendered"
    assert len(body["scripts"]) == 1
    assert len(body["scripts"][0]["variant_label"]) == _VARIANT_LABEL_MAX_LENGTH
    assert body["scripts"][0]["variant_label"] == LONG_LABEL[:_VARIANT_LABEL_MAX_LENGTH]
    assert body["video"] is not None
