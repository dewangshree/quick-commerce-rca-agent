"""API integration tests: auth, RBAC, validation, analytics, investigations, reports, rate limit, audit."""
import pytest
from sqlalchemy import text

from qc_rca.config import get_settings
from qc_rca.db.session import get_readonly_engine


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_enabled", False)


def hdr(key):
    return {"X-API-Key": key}


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["database"] is True
    assert "X-Request-ID" in r.headers


def test_missing_and_invalid_key_rejected(client):
    assert client.get("/api/v1/analytics/meta").status_code == 401
    assert client.get("/api/v1/analytics/meta", headers=hdr("qc_wrong")).status_code == 401


def test_rbac(client, api_keys):
    assert client.get("/api/v1/analytics/meta", headers=hdr(api_keys["viewer"])).status_code == 200
    r = client.post("/api/v1/investigations", json={"question": "What is OR2A?"}, headers=hdr(api_keys["viewer"]))
    assert r.status_code == 403
    assert client.get("/api/v1/admin/audit", headers=hdr(api_keys["analyst"])).status_code == 403
    assert client.get("/api/v1/admin/audit", headers=hdr(api_keys["admin"])).status_code == 200


def test_analytics_endpoints(client, api_keys):
    h = hdr(api_keys["viewer"])
    ov = client.get("/api/v1/analytics/overview", params={"date": "2026-04-22"}, headers=h).json()
    assert ov["overview"]["total_orders"] > 0 and len(ov["by_hour"]) > 10
    cities = client.get("/api/v1/analytics/cities", headers=h).json()
    assert len(cities) == 9
    assert sum(c["total_orders"] for c in cities) == ov["overview"]["total_orders"]
    stores = client.get("/api/v1/analytics/cities/Bangalore/stores", headers=h).json()
    assert stores["stores"][0]["breach_rate"] >= stores["stores"][-1]["breach_rate"]
    rca = client.get("/api/v1/analytics/stores/STORE_101/rca", headers=h).json()
    assert rca["rca"]["summary"]["total_orders"] == 397
    hours = client.get("/api/v1/analytics/stores/STORE_101/hours", params={"hour_from": 6, "hour_to": 11},
                       headers=h).json()
    assert [x["hour"] for x in hours["hours"]] == [6, 7, 8, 9, 10, 11]
    cmp_ = client.get("/api/v1/analytics/stores/compare", params={"stores": "STORE_101,STORE_102"}, headers=h).json()
    assert [c["store"] for c in cmp_] == ["STORE_101", "STORE_102"]
    top = client.get("/api/v1/analytics/top-stores", params={"limit": 5}, headers=h).json()
    assert len(top) == 5


def test_input_validation(client, api_keys):
    h = hdr(api_keys["viewer"])
    assert client.get("/api/v1/analytics/stores/STORE_101/hours", params={"hour_from": 30}, headers=h).status_code == 422
    assert client.get("/api/v1/analytics/overview", params={"city": "x'; drop table--"}, headers=h).status_code == 422
    assert client.get("/api/v1/analytics/overview", params={"date": "not-a-date"}, headers=h).status_code == 422
    r = client.get("/api/v1/analytics/stores/STORE_999/rca", headers=h)
    assert r.status_code == 404 and "suggestions" in r.json()
    r = client.post("/api/v1/investigations", json={"question": ""}, headers=hdr(api_keys["analyst"]))
    assert r.status_code == 422


def test_investigation_history_and_report_download(client, api_keys):
    h = hdr(api_keys["analyst"])
    r = client.post("/api/v1/investigations", json={"question": "Why did STORE_101 underperform?",
                                                    "thread_id": "t-1"}, headers=h)
    assert r.status_code == 200
    rep = r.json()
    iid = rep["investigation_id"]
    follow = client.post("/api/v1/investigations", json={"question": "Walk me through the morning hours",
                                                         "thread_id": "t-1"}, headers=h).json()
    assert follow["scope"]["stores"] == ["STORE_101"]  # context from thread history
    hist = client.get("/api/v1/investigations", headers=h).json()
    assert iid in [x["id"] for x in hist]
    md = client.get(f"/api/v1/investigations/{iid}/report.md", headers=h)
    assert md.status_code == 200 and "attachment" in md.headers["content-disposition"]
    assert "## Probable contributing factors" in md.text and "## Sources" in md.text
    # another analyst cannot see it; admin can
    assert client.get(f"/api/v1/investigations/{iid}", headers=hdr(api_keys["viewer"])).status_code == 403
    assert client.get(f"/api/v1/investigations/{iid}", headers=hdr(api_keys["admin"])).status_code == 200


def test_legacy_chat_endpoint(client, api_keys):
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "How did Bangalore do?"}]},
                    headers=hdr(api_keys["analyst"]))
    assert r.status_code == 200 and "Bangalore" in r.json()["reply"]


def test_rate_limit(client, api_keys, monkeypatch):
    from qc_rca.security import auth

    monkeypatch.setattr(auth, "_limiter", auth.RateLimiter(2))
    h = hdr(api_keys["analyst"])
    codes = [client.post("/api/v1/investigations", json={"question": "hello"}, headers=h).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429]


def test_audit_log_written(client, api_keys):
    client.get("/api/v1/analytics/meta", headers=hdr(api_keys["viewer"]))
    rows = client.get("/api/v1/admin/audit", headers=hdr(api_keys["admin"])).json()
    assert any(r["path"] == "/api/v1/analytics/meta" and r["user"] == "test-viewer" for r in rows)


def test_readonly_engine_cannot_write():
    with pytest.raises(Exception) as exc:
        with get_readonly_engine().begin() as c:
            c.execute(text("DELETE FROM stores"))
    assert "read-only" in str(exc.value) or "permission denied" in str(exc.value)


def test_knowledge_search_endpoint(client, api_keys):
    r = client.post("/api/v1/knowledge/search", json={"query": "sustained pileup escalation", "k": 3,
                                                      "doc_types": ["sop", "sla"]}, headers=hdr(api_keys["analyst"]))
    assert r.status_code == 200
    chunks = r.json()["chunks"]
    assert chunks and all(c["doc_type"] in ("sop", "sla") for c in chunks)


def test_server_error_keeps_cors_headers(client, api_keys, monkeypatch):
    # Without CORS headers on a 500 the browser only shows "Failed to fetch".
    from qc_rca.analytics import service

    def boom(*a, **k):
        raise RuntimeError("secret internals")
    monkeypatch.setattr(service, "list_cities", boom)
    r = client.get("/api/v1/analytics/meta", headers={**hdr(api_keys["viewer"]), "Origin": "http://localhost:5173"})
    assert r.status_code == 500
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "secret" not in r.text and r.json()["request_id"]


def test_production_guards(monkeypatch):
    from qc_rca.config import Settings

    with pytest.raises(ValueError, match="AUTH_ENABLED"):
        Settings(env="production", auth_enabled=False, api_key_pepper="a-real-secret-value")
    with pytest.raises(ValueError, match="API_KEY_PEPPER"):
        Settings(env="production", auth_enabled=True, api_key_pepper="change-me")
    ok = Settings(env="production", auth_enabled=True, api_key_pepper="a-real-secret-value")
    assert ok.auth_enabled


def test_docs_hidden_in_production(monkeypatch):
    from fastapi.testclient import TestClient

    from qc_rca.api.main import create_app

    monkeypatch.setattr(get_settings(), "env", "production")
    c = TestClient(create_app())
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404


def test_public_demo_mode(client, api_keys, monkeypatch):
    from qc_rca.security import auth

    monkeypatch.setattr(get_settings(), "public_demo", True)
    monkeypatch.setattr(auth, "_limiter", auth.RateLimiter(2))
    a, b = {"X-Visitor-Id": "visitor-aaaaaaaa"}, {"X-Visitor-Id": "visitor-bbbbbbbb"}
    assert client.get("/api/v1/analytics/meta").status_code == 200          # no key needed
    r = client.post("/api/v1/investigations", json={"question": "hello"}, headers=a)
    assert r.status_code == 200
    mine = client.get("/api/v1/investigations", headers=a).json()
    assert r.json()["investigation_id"] in [x["id"] for x in mine]
    assert r.json()["investigation_id"] not in [x["id"] for x in client.get("/api/v1/investigations", headers=b).json()]
    # rate limit is per IP, so switching visitor id does not reset it
    assert client.post("/api/v1/investigations", json={"question": "hello"}, headers=b).status_code == 200
    assert client.post("/api/v1/investigations", json={"question": "hello"}, headers={"X-Visitor-Id": "visitor-cccccccc"}).status_code == 429
    # admin stays locked; a wrong key is still rejected (no silent fallback to visitor)
    assert client.get("/api/v1/admin/audit").status_code == 403
    assert client.get("/api/v1/analytics/meta", headers=hdr("qc_wrong")).status_code == 401
    assert client.get("/api/v1/admin/audit", headers=hdr(api_keys["admin"])).status_code == 200


def test_frontend_served_at_root(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from qc_rca.api.main import create_app

    (tmp_path / "index.html").write_text("<!doctype html><title>QC</title>")
    monkeypatch.setattr(get_settings(), "frontend_dist", tmp_path)
    c = TestClient(create_app())
    assert "<title>QC</title>" in c.get("/").text
    assert c.get("/health").json()["database"] is True   # API routes still win
