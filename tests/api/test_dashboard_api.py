def test_dashboard_summary_reflects_pipeline_state(client):
    resp = client.get("/dashboard/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["campaign_count"] == 0
    assert body["pending_review_count"] == 0

    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    client.post(f"/scripts/{scripts[0]['id']}/render", json={})

    summary = client.get("/dashboard/summary").json()
    assert summary["campaign_count"] == 1
    assert summary["pending_review_count"] == 1
    assert summary["video_counts_by_status"].get("pending_review") == 1


def test_dashboard_ui_serves_the_operator_frontend(client):
    """The success criterion for the frontend work: opening the site shows
    a working multi-page dashboard, not just Swagger. This is a static
    file (unauthenticated, excluded from the OpenAPI schema — see
    api/main.py), so it just needs to serve the real page and expose every
    required page's route in its client-side router."""
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    body = resp.text
    for route in ["#/login", "#/dashboard", "#/campaigns", "#/pipeline", "#/ideas", "#/scripts", "#/videos", "#/settings"]:
        assert f"'{route}'" in body


def test_dashboard_settings_reports_provider_status_without_secrets(client):
    resp = client.get("/dashboard/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_provider_configured"] in {"anthropic", "groq", "fake"}
    assert body["llm_provider_effective"] in {"anthropic", "groq", "fake"}
    assert body["renderer_backend"] in {"template_pillow", "null"}
    assert body["environment"] == "development"
    assert "api_key" not in str(body).lower()
    assert "secret" not in str(body).lower()


def test_dashboard_settings_flags_silent_fallback_when_api_key_is_missing(client, monkeypatch):
    """Regression test for a real bug: this endpoint used to show only the
    *configured* provider (e.g. "groq"), which stayed "groq" even when
    config.py's own documented fallback (no API key -> silently use the
    fake, zero-content provider) had kicked in — making it impossible to
    tell from this page alone why every pipeline stage was returning
    empty results. It must now report the truth: what's actually running."""
    from content_factory.config import get_settings

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "")
    get_settings.cache_clear()
    try:
        resp = client.get("/dashboard/settings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["llm_provider_configured"] == "groq"
        assert body["llm_provider_effective"] == "fake"
        assert body["llm_provider_using_fallback"] is True
    finally:
        get_settings.cache_clear()


def test_dashboard_settings_requires_authentication(unauthenticated_client):
    resp = unauthenticated_client.get("/dashboard/settings")
    assert resp.status_code == 401


def test_health_endpoint(client):
    """Production Hardening Sprint H6: /health now reports a per-dependency
    checks dict (previously a bare {"status": "ok"}), so a caller can tell
    *which* dependency is down instead of just that something is."""
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    # Redis isn't configured in the test environment (rate_limit_backend
    # defaults to "memory"), so it's correctly absent, not falsely "ok".
    assert "redis" not in body["checks"]


def test_health_endpoint_reports_unhealthy_when_database_is_unreachable(client, monkeypatch):
    from content_factory.api import main as main_module

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("simulated database outage")

    monkeypatch.setattr(main_module, "engine", _BrokenEngine())
    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unhealthy"
    assert body["checks"]["database"] == "unreachable"


def test_health_endpoint_includes_redis_check_when_configured(client, monkeypatch):
    from content_factory.config import get_settings

    monkeypatch.setenv("RATE_LIMIT_BACKEND", "redis")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    get_settings.cache_clear()
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["checks"]["redis"] == "ok"
    finally:
        get_settings.cache_clear()


def test_health_endpoint_reports_the_resolved_clip_factory_pipeline_config(client, monkeypatch):
    """Regression test for a real production incident: TRANSCRIPTION_PROVIDER
    and CLIP_RENDERER_BACKEND were never set in render.yaml, so the live
    site silently ran the entire mandated clip-factory pipeline on
    NullTranscriptionProvider/NullClipRenderer with no error anywhere - the
    only way to have caught it short of running the whole pipeline and
    noticing the output was a placeholder. /health now reports the actual
    resolved provider for every stage so this class of gap is visible from
    a single unauthenticated GET, matching what Render's own healthCheckPath
    already polls every 30s."""
    from content_factory.config import get_settings

    # Explicitly forced (rather than relying on ambient .env content, which
    # a local dev environment may have populated with real credentials for
    # its own manual testing) so this asserts the *resolution* logic itself,
    # deterministically, regardless of what's in any given .env.
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "null")
    monkeypatch.setenv("CLIP_RENDERER_BACKEND", "null")
    monkeypatch.setenv("MEDIA_BACKUP_ENABLED", "false")
    get_settings.cache_clear()
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        pipeline = resp.json()["pipeline"]
        # Every resolved provider correctly reports its safe fallback - this
        # asserts the field is genuinely *resolved* (would reflect
        # "null"/"fake" here), not just echoing the raw configured value.
        assert pipeline["transcription_provider"] == "null"
        assert pipeline["clip_renderer_backend"] == "null"
        assert pipeline["llm_provider"] == "fake"
        assert pipeline["media_backup_enabled"] is False
        assert pipeline["media_backup_publicly_hostable"] is False
        assert pipeline["publishing_enabled"] is True
    finally:
        get_settings.cache_clear()


def test_health_endpoint_reports_which_publishing_platforms_have_credentials_configured(client, monkeypatch):
    """Real gap this closes: whether auto-publish reaches a real platform
    provider or falls back to ManualPublishingProvider ("scheduled") used
    to only be inferable after the fact, from one publish attempt's own
    detail message. This makes the platform-level gate
    (publishing/factory.py's _PLATFORM_CREDENTIAL_CHECK) visible from a
    single unauthenticated GET, matching every other pipeline-config field
    already in /health. Only booleans are ever reported - never the
    credential values themselves."""
    from content_factory.config import get_settings

    monkeypatch.setenv("TIKTOK_CLIENT_KEY", "")
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "")
    monkeypatch.setenv("INSTAGRAM_APP_ID", "a-real-app-id")
    get_settings.cache_clear()
    try:
        resp = client.get("/health")
        assert resp.status_code == 200
        creds = resp.json()["pipeline"]["publishing_platform_credentials_configured"]
        assert creds == {"tiktok": False, "youtube": False, "instagram": True}
        assert "a-real-app-id" not in resp.text
    finally:
        get_settings.cache_clear()


def test_health_endpoint_pipeline_config_never_affects_the_liveness_status(client, monkeypatch):
    """A placeholder provider is a real configuration problem, but it is
    not the same thing as "this instance is unreachable" - Render's own
    healthCheckPath points at /health, so reporting the pipeline as
    misconfigured must never also flip status/status_code, or a bad
    TRANSCRIPTION_PROVIDER value would make Render kill/restart-loop an
    otherwise-healthy instance."""
    from content_factory.api import main as main_module

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("simulated database outage")

    monkeypatch.setattr(main_module, "engine", _BrokenEngine())
    resp = client.get("/health")
    assert resp.status_code == 503
    assert "pipeline" in resp.json()


def test_response_includes_a_request_id_header(client):
    """Production Hardening Sprint H6: request-ID correlation middleware —
    every response carries an X-Request-ID, generated if the caller didn't
    supply one."""
    resp = client.get("/health")
    assert resp.headers.get("X-Request-ID")


def test_response_echoes_a_caller_supplied_request_id(client):
    resp = client.get("/health", headers={"X-Request-ID": "caller-supplied-id-123"})
    assert resp.headers["X-Request-ID"] == "caller-supplied-id-123"


def test_metrics_endpoint_is_exposed(client):
    """Production Hardening Sprint H6: Prometheus metrics, on by default
    since prometheus-fastapi-instrumentator is in the test environment."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
