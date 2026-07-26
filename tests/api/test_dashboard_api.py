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
