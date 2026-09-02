"""A1 (POA/15 §7) — the shared service template scaffolds a module with
health, readiness, metrics, correlation-id and error handling wired in.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from services.platform import create_app


def test_healthz_liveness():
    client = TestClient(create_app("test-svc"))
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "service": "test-svc"}


def test_readyz_is_ready_with_no_checks():
    client = TestClient(create_app("test-svc"))
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


def test_readyz_503_when_a_check_fails():
    client = TestClient(create_app("test-svc", readiness_checks={"dep": lambda: False}))
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["status"] == "not_ready"
    assert r.json()["checks"]["dep"] is False


def test_readyz_supports_async_checks():
    async def ok() -> bool:
        return True

    client = TestClient(create_app("test-svc", readiness_checks={"dep": ok}))
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"]["dep"] is True


def test_metrics_endpoint_exposes_prometheus_text():
    client = TestClient(create_app("metrics-svc"))
    client.get("/healthz")  # generate at least one measured request
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
    assert "http_request_duration_seconds" in r.text


def test_correlation_id_is_generated_and_echoed():
    client = TestClient(create_app("cid-svc"))
    r = client.get("/healthz")
    assert r.headers.get("X-Request-ID")                      # minted when absent
    r2 = client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert r2.headers["X-Request-ID"] == "abc123"             # echoed when supplied


def test_unhandled_error_returns_problem_json():
    app = create_app("err-svc")

    @app.get("/boom")
    def boom():
        raise ValueError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["title"] == "Internal Server Error"
    assert "correlation_id" in body


def test_event_pipeline_app_boots_on_the_template():
    from services.event_pipeline.main import app as event_app

    client = TestClient(event_app)
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["track"] == "A"
    assert client.get("/healthz").status_code == 200
