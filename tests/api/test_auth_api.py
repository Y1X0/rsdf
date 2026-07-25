"""Regression tests for P0-3 (PHASE1_AUDIT.md F2 — no authentication or
authorization on any endpoint)."""

from content_factory.auth.jwt_service import create_access_token
from content_factory.config import get_settings


def test_token_issued_for_valid_credentials(unauthenticated_client):
    resp = unauthenticated_client.post(
        "/auth/token", json={"client_id": "test-operator", "client_secret": "test-operator-secret"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    assert isinstance(body["access_token"], str) and body["access_token"]


def test_token_rejected_for_invalid_client_secret(unauthenticated_client):
    resp = unauthenticated_client.post(
        "/auth/token", json={"client_id": "test-operator", "client_secret": "wrong-secret"}
    )
    assert resp.status_code == 401


def test_token_rejected_for_unknown_client_id(unauthenticated_client):
    resp = unauthenticated_client.post(
        "/auth/token", json={"client_id": "someone-else", "client_secret": "test-operator-secret"}
    )
    assert resp.status_code == 401


def test_business_endpoint_rejects_missing_token(unauthenticated_client):
    resp = unauthenticated_client.get("/campaigns")
    assert resp.status_code == 401
    assert resp.headers.get("www-authenticate") == "Bearer"


def test_business_endpoint_rejects_malformed_authorization_header(unauthenticated_client):
    resp = unauthenticated_client.get("/campaigns", headers={"Authorization": "NotBearer abc123"})
    assert resp.status_code == 401


def test_business_endpoint_rejects_invalid_token(unauthenticated_client):
    resp = unauthenticated_client.get("/campaigns", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert resp.status_code == 401


def test_business_endpoint_rejects_expired_token(unauthenticated_client):
    # Build an already-expired token directly via the signing library rather
    # than sleeping in the test.
    from datetime import UTC, datetime, timedelta

    import jwt as pyjwt

    settings = get_settings()
    expired_payload = {
        "sub": "test-operator",
        "role": "operator",
        "iat": datetime.now(UTC) - timedelta(minutes=2),
        "exp": datetime.now(UTC) - timedelta(minutes=1),
    }
    expired_token = pyjwt.encode(expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    resp = unauthenticated_client.get("/campaigns", headers={"Authorization": f"Bearer {expired_token}"})
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


def test_valid_token_grants_access_to_read_endpoint(client):
    resp = client.get("/campaigns")
    assert resp.status_code == 200


def test_non_operator_role_is_forbidden_from_write_endpoints(unauthenticated_client):
    settings = get_settings()
    viewer_token = create_access_token(subject="read-only-client", role="viewer", settings=settings)

    resp = unauthenticated_client.post(
        "/campaigns",
        json={"brand_name": "Acme"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert resp.status_code == 403

    # But a read endpoint still works for the same non-operator token.
    resp = unauthenticated_client.get("/campaigns", headers={"Authorization": f"Bearer {viewer_token}"})
    assert resp.status_code == 200


def test_review_endpoint_uses_authenticated_identity_not_request_body(client):
    """PHASE1_AUDIT.md F2's specific manifestation on the review endpoint:
    a client used to be able to submit any `reviewer_id` string and have it
    trusted verbatim. Now the persisted reviewer_id must always be the
    authenticated principal's subject, regardless of what the body claims."""
    campaign = client.post("/campaigns", json={"brand_name": "Acme", "cpm_rate": 3.0}).json()
    idea = client.post(f"/campaigns/{campaign['id']}/ideas", json={"concept_summary": "idea"}).json()
    scripts = client.post(f"/ideas/{idea['id']}/scripts", json={"num_variants": 1}).json()
    video = client.post(f"/scripts/{scripts[0]['id']}/render", json={}).json()

    resp = client.post(
        f"/videos/{video['id']}/review",
        json={"reviewer_id": "someone-i-am-impersonating", "decision": "approved"},
    )
    assert resp.status_code == 200
    assert resp.json()["reviewer_id"] == "test-operator"
    assert resp.json()["reviewer_id"] != "someone-i-am-impersonating"
