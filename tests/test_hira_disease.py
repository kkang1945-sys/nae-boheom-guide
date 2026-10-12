from pathlib import Path
from dataclasses import replace
from urllib.parse import parse_qs, urlparse

import pytest

from insurance_app.config import load_settings
from insurance_app.hira_disease import HiraDiseaseClient, HiraDiseaseError, parse_hira_xml
from test_pdf import isolated_app


SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
<body><items><item><sickCd>C50</sickCd><year>2024</year><patCnt>123</patCnt></item></items>
<numOfRows>100</numOfRows><pageNo>1</pageNo><totalCount>1</totalCount></body></response>"""


class Response:
    def __init__(self, payload=SAMPLE_XML):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def test_parse_hira_xml_keeps_public_rows_and_rejects_error():
    parsed = parse_hira_xml(SAMPLE_XML)
    assert parsed["total_count"] == 1
    assert parsed["items"] == [{"sickCd": "C50", "year": "2024", "patCnt": "123"}]
    with pytest.raises(HiraDiseaseError, match="20"):
        parse_hira_xml(b"<response><header><resultCode>20</resultCode></header></response>")


def test_client_uses_environment_key_without_putting_it_in_cache(tmp_path):
    calls = []

    def opener(url, timeout):
        calls.append((url, timeout))
        return Response()

    secret = "abc+123=="
    client = HiraDiseaseClient(secret, tmp_path / "cache", opener=opener)
    first = client.inpatient_outpatient("c50", 2024)
    second = client.inpatient_outpatient("C50", 2024)

    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert len(calls) == 1
    query = parse_qs(urlparse(calls[0][0]).query)
    assert query["ServiceKey"] == [secret]
    assert query["sickCd"] == ["C50"]
    cache_text = next((tmp_path / "cache").iterdir()).read_text(encoding="utf-8")
    assert secret not in cache_text
    assert "ServiceKey" not in cache_text


def test_client_accepts_encoded_key_and_validates_public_parameters(tmp_path):
    seen = []
    client = HiraDiseaseClient("abc%2B123%3D%3D", tmp_path, opener=lambda url, timeout: seen.append(url) or Response())
    client.gender_age("I63", 2024)
    assert parse_qs(urlparse(seen[0]).query)["ServiceKey"] == ["abc+123=="]
    with pytest.raises(ValueError, match="질병코드"):
        client.gender_age("잘못된 코드", 2024)
    with pytest.raises(ValueError, match="연도"):
        client.gender_age("I63", 1900)


def test_settings_reads_key_only_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HIRA_DISEASE_API_KEY", "local-secret")
    settings = load_settings(Path(tmp_path))
    assert settings.hira_disease_api_key == "local-secret"


def test_medical_statistics_page_does_not_expose_or_require_key(isolated_app, monkeypatch):
    client, _, _ = isolated_app
    import insurance_app.main as main

    # This case verifies the unconfigured state regardless of the developer
    # machine's real user-level environment variable.
    monkeypatch.setattr(main, "settings", replace(main.settings, hira_disease_api_key=None))
    page = client.get("/medical-statistics")
    assert page.status_code == 200
    assert "API 키 미설정" in page.text
    assert page.headers["cache-control"] == "no-store"
    failed = client.get("/medical-statistics?sick_code=C50&year=2024")
    assert "환경변수" in failed.text


def test_medical_statistics_page_shows_safe_public_result(isolated_app, monkeypatch):
    client, _, _ = isolated_app
    import insurance_app.main as main

    monkeypatch.setattr(main, "settings", replace(main.settings, hira_disease_api_key="never-show-this"))

    class FakeClient:
        def __init__(self, service_key, cache_dir):
            assert service_key == "never-show-this"

        def inpatient_outpatient(self, sick_code, year):
            assert (sick_code, year) == ("C50", 2024)
            return {"total_count": 1, "items": [{"sickCd": "C50", "patCnt": "123"}],
                    "cache_hit": False, "fetched_at": "2026-09-16T00:00:00+00:00",
                    "verified_on": "2026-09-16", "scope": "참고 통계",
                    "source_url": "https://www.data.go.kr/data/15119055/openapi.do"}

    monkeypatch.setattr(main, "HiraDiseaseClient", FakeClient)
    page = client.get("/medical-statistics?sick_code=C50&year=2024")
    assert page.status_code == 200
    assert "123" in page.text and "참고 통계" in page.text
    assert "never-show-this" not in page.text
