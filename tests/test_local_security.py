from fastapi.testclient import TestClient

from insurance_app.main import app


def test_sensitive_pages_and_downloads_are_not_cached():
    with TestClient(app) as client:
        for path in ("/", "/subjects", "/financial-info", "/results", "/data/delete"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["pragma"] == "no-cache"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["referrer-policy"] == "no-referrer"
            assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_cross_origin_browser_write_is_rejected_without_changing_data():
    with TestClient(app) as client:
        rejected = client.post("/data/delete", data={"confirm_delete": "DELETE_ALL"},
                               headers={"Origin": "https://untrusted.example"})
        assert rejected.status_code == 403
        assert "차단" in rejected.text
        same_origin = client.post("/data/delete", data={"confirm_delete": "wrong"},
                                  headers={"Origin": "http://testserver"})
        assert same_origin.status_code == 422
        loopback_alias = client.post(
            "/data/delete",
            data={"confirm_delete": "wrong"},
            headers={"Origin": "http://localhost", "Host": "127.0.0.1"},
        )
        assert loopback_alias.status_code == 422
        in_app_browser = client.post(
            "/data/delete",
            data={"confirm_delete": "wrong"},
            headers={
                "Origin": "null", "Host": "127.0.0.1",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        assert in_app_browser.status_code == 422
        opaque_cross_site = client.post(
            "/data/delete",
            data={"confirm_delete": "DELETE_ALL"},
            headers={
                "Origin": "null", "Host": "127.0.0.1",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        assert opaque_cross_site.status_code == 403
        assert rejected.encoding == "utf-8"
        assert client.get("/health", headers={"Origin": "https://untrusted.example"}).status_code == 200
