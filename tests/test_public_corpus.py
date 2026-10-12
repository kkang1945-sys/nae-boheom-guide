import hashlib
import pymupdf

from insurance_app.coverage_rules import standardize_coverage_name
from insurance_app.public_corpus import analyze_pdf, build_report, load_manifest, validate_manifest


def test_tracked_public_manifest_is_valid():
    manifest = load_manifest(__import__("pathlib").Path("corpus/public_sources.json"))

    assert len(manifest["sources"]) == 5
    assert {source["provider"] for source in manifest["sources"]} >= {"KDB생명", "삼성화재", "AI-Hub"}
    assert all(source["redistribution"] != "allowed" for source in manifest["sources"])


def test_manifest_rejects_non_https_source():
    invalid = {
        "schema_version": 1,
        "sources": [{
            "id": "bad", "source_type": "ocr_dataset", "access": "download_application_required",
            "landing_url": "http://example.test/data", "local_filename": None,
        }],
    }

    try:
        validate_manifest(invalid)
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:
        raise AssertionError("비 HTTPS 출처를 허용했습니다.")


def test_report_separates_gated_source_and_analyzes_verified_pdf(tmp_path):
    pdf_path = tmp_path / "sample.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "brain coverage")
    document.save(pdf_path)
    expected_hash = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    manifest = {
        "schema_version": 1,
        "sources": [
            {
                "id": "local", "source_type": "policy_terms", "access": "public_direct_download",
                "landing_url": "https://example.test", "local_filename": "sample.pdf", "sha256": expected_hash,
            },
            {
                "id": "gated", "source_type": "ocr_dataset", "access": "download_application_required",
                "landing_url": "https://example.test/gated", "local_filename": None,
            },
        ],
    }
    validate_manifest(manifest)

    report = build_report(manifest, tmp_path)

    assert report["sources"][0]["status"] == "analyzed"
    assert report["sources"][0]["page_count"] == 1
    assert report["sources"][1]["status"] == "manual_download_required"


def test_pdf_hash_mismatch_is_rejected(tmp_path):
    path = tmp_path / "changed.pdf"
    path.write_bytes(b"%PDF-changed")

    try:
        analyze_pdf(path, "0" * 64)
    except ValueError as error:
        assert "SHA-256" in str(error)
    else:
        raise AssertionError("변경된 공개 원본을 허용했습니다.")


def test_care_benefit_with_hospital_setting_stays_in_care_group():
    assert standardize_coverage_name("상해 입원 간병인 사용일당") == "간병"
    assert standardize_coverage_name("입원 간호·간병통합서비스 사용일당") == "간병"
    assert standardize_coverage_name("간병인 입원 지원") is None
