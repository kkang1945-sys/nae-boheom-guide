from urllib.parse import urlparse
from types import SimpleNamespace

from insurance_app.public_evidence import PUBLIC_EVIDENCE_SCOPE, VERIFIED_ON, public_program_evidence
from insurance_app.result_export import result_pdf
from test_pdf import isolated_app  # noqa: F401 - pytest fixture


def test_public_program_catalog_uses_only_official_https_sources():
    programs = public_program_evidence()
    assert {item["id"] for item in programs} == {
        "copayment_ceiling", "special_copayment", "catastrophic_medical_expense", "long_term_care",
        "infant_health_screening", "pregnancy_childbirth_medical", "national_pension_old_age",
    }
    for item in programs:
        parsed = urlparse(item["url"])
        assert parsed.scheme == "https"
        assert parsed.hostname in {"www.nhis.or.kr", "www.nps.or.kr"}
        assert item["verified_on"] == VERIFIED_ON
        assert not any(char.isdigit() for char in item["summary"] + item["caveat"])


def _subject(life_stage, birth_year, pregnancy="not_applicable", sex="both"):
    return SimpleNamespace(life_stage=life_stage, birth_year=birth_year,
                           pregnancy_childbirth_review=pregnancy, sex_specific_review=sex)


def test_public_public_programs_are_prioritized_without_assuming_eligibility():
    child_ids = {item["id"] for item in public_program_evidence(_subject("child", 2022))}
    older = {item["id"]: item for item in public_program_evidence(
        _subject("retired_older_adult", 1950))}
    pregnant_ids = {item["id"] for item in public_program_evidence(
        _subject("young_adult", 1998, pregnancy="include", sex="female_related"))}

    assert "infant_health_screening" in child_ids
    assert "national_pension_old_age" not in child_ids
    assert {"long_term_care", "national_pension_old_age"} <= set(older)
    assert "우선 확인" in older["long_term_care"]["relevance"]
    assert "pregnancy_childbirth_medical" in pregnant_ids
    assert "pregnancy_childbirth_medical" not in {
        item["id"] for item in public_program_evidence(_subject("young_adult", 1998))
    }


def test_public_programs_are_reference_only_and_page_is_unlocked(isolated_app):
    client, _, _ = isolated_app
    response = client.get("/evidence")
    assert response.status_code == 200
    assert "본인부담상한제" in response.text
    assert "재난적의료비 지원사업" in response.text
    assert "확인일 2026-09-08" in response.text
    assert "기능 준비 중" not in response.text
    assert "자금공백에서 차감하지 않" in PUBLIC_EVIDENCE_SCOPE
    assert "민간보험 가입금액을 권장" in PUBLIC_EVIDENCE_SCOPE


def test_pdf_preserves_official_source_and_non_deduction_notice():
    evidence = {"inputs": [], "formulae": [], "public_programs": public_program_evidence(),
                "public_evidence_scope": PUBLIC_EVIDENCE_SCOPE}
    report = {"version": "test", "scope": "보험금 지급 여부를 판단하지 않습니다.",
              "scenarios": [], "blocked_documents": []}
    content = result_pdf("익명 대상", report, evidence=evidence)
    import pymupdf
    with pymupdf.open(stream=content) as pdf:
        text = "".join(page.get_text() for page in pdf)
    compact = "".join(text.split())
    assert "본인부담상한제" in text
    assert "www.nhis.or.kr" in text
    assert "공적제도 확인 자료" in text
    assert "자금공백에서차감하지않" in compact
    assert "민간보험가입금액을권장" in compact
