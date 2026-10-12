import pytest

from insurance_app.database import create_schema, create_session_factory
from insurance_app.financial_profiles import (
    financial_readiness, save_financial_profile, transient_financial_profile,
)
from insurance_app.subjects import save_subject
from test_pdf import isolated_app


def subject_values():
    return dict(label="나", relationship_to_user="self", birth_year=1990, life_stage="working",
                economic_role="primary_earner", sex_specific_review="both", pregnancy_childbirth_review="not_applicable")


def profile_values(**changes):
    values = {"monthly_essential_expenses_won": "3000000", "monthly_net_income_won": "5000000",
              "income_gap_months": "6", "emergency_fund_available_won": "10000000",
              "emergency_fund_to_use_won": "3000000", "paid_leave_support_won": "0",
              "loan_balance_won": "0", "dependent_count": "0", "family_monthly_expenses_won": "",
              "family_support_months": "", "liquid_assets_to_use_won": "",
              "guardian_monthly_income_loss_won": "", "child_monthly_care_cost_won": "",
              "retirement_monthly_income_to_use_won": "", "retirement_monthly_care_cost_won": ""}
    return values | changes


def test_profile_preserves_empty_as_unknown_and_validates_fund_range(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        subject = save_subject(session, **subject_values())
        profile = save_financial_profile(session, subject, profile_values())
        assert profile.family_support_months is None
        assert financial_readiness(profile)["income_gap_ready"]
        with pytest.raises(ValueError, match="비상자금"):
            save_financial_profile(session, subject, profile_values(emergency_fund_to_use_won="10000001"))


def test_transient_profile_validates_without_database_row(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    profile = transient_financial_profile(profile_values())
    assert profile.monthly_essential_expenses_won == 3_000_000
    assert profile.family_support_months is None
    with factory() as session:
        models = __import__('insurance_app.models', fromlist=['FinancialProfile'])
        assert session.query(models.FinancialProfile).count() == 0
    with pytest.raises(ValueError, match="비상자금"):
        transient_financial_profile(profile_values(emergency_fund_to_use_won="10000001"))


def test_financial_screen_saves_only_for_existing_subject(isolated_app):
    client, factory, _ = isolated_app
    created = client.post('/subjects', data=subject_values())
    assert created.status_code == 200
    with factory() as session:
        subject_id = session.query(__import__('insurance_app.models', fromlist=['AnalysisSubject']).AnalysisSubject).one().id
    page = client.get('/financial-info')
    assert page.status_code == 200
    assert '판단 보류' in page.text
    saved = client.post(f'/subjects/{subject_id}/financial-profile', data=profile_values())
    assert saved.status_code == 200
    assert '경제정보를 저장했습니다' in saved.text
    assert client.post('/subjects/99999/financial-profile', data=profile_values()).status_code == 404


def test_life_stage_readiness_requires_explicit_child_and_retirement_inputs(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        child = save_subject(session, **(subject_values() | {
            "label": "아이", "relationship_to_user": "child", "birth_year": 2020,
            "life_stage": "child", "economic_role": "dependent",
        }))
        child_profile = save_financial_profile(session, child, profile_values(
            monthly_essential_expenses_won="", guardian_monthly_income_loss_won="2000000",
            child_monthly_care_cost_won="500000"))
        child_ready = financial_readiness(child_profile, child.life_stage)
        assert child_ready["income_gap_ready"]
        assert "보호자" in child_ready["basis"]

        retired = save_subject(session, **(subject_values() | {
            "label": "부모님", "relationship_to_user": "parent", "birth_year": 1950,
            "life_stage": "retired_older_adult", "economic_role": "retired",
        }))
        retired_profile = save_financial_profile(session, retired, profile_values(
            paid_leave_support_won="", retirement_monthly_income_to_use_won="1200000",
            retirement_monthly_care_cost_won="300000"))
        retired_ready = financial_readiness(retired_profile, retired.life_stage)
        assert retired_ready["income_gap_ready"]
        assert "연금" in retired_ready["basis"]


def test_financial_screen_selects_inputs_by_life_stage(isolated_app):
    client, _, _ = isolated_app
    child_values = subject_values() | {
        "label": "아이", "relationship_to_user": "child", "birth_year": 2020,
        "life_stage": "child", "economic_role": "dependent",
    }
    assert client.post('/subjects', data=child_values).status_code == 200
    page = client.get('/financial-info')
    assert '보호자 월 소득 감소액' in page.text
    assert '아동 월 추가 돌봄비' in page.text
    assert '분석에 사용할 월 연금·지속소득' not in page.text


def test_retired_financial_screen_asks_for_ongoing_income_and_care(isolated_app):
    client, _, _ = isolated_app
    retired_values = subject_values() | {
        "label": "부모님", "relationship_to_user": "parent", "birth_year": 1950,
        "life_stage": "retired_older_adult", "economic_role": "retired",
    }
    assert client.post('/subjects', data=retired_values).status_code == 200
    page = client.get('/financial-info')
    assert '분석에 사용할 월 연금·지속소득' in page.text
    assert '월 의료·돌봄 본인부담' in page.text
    assert '보호자 월 소득 감소액' not in page.text


def test_mock_analysis_form_is_no_store_and_does_not_create_profile(isolated_app, monkeypatch):
    client, factory, _ = isolated_app
    import insurance_app.main as main
    from insurance_app.models import AnalysisSubject, FinancialProfile, InsuranceDocument

    with factory.begin() as session:
        document = InsuranceDocument(source_name="형식검증.pdf", source_path="external.pdf",
            sha256="f" * 64, file_size=10, format="pdf", metadata_review_status="confirmed")
        session.add(document)
        session.flush()
        document_id = document.id

    monkeypatch.setattr(main, "subject_funding_results", lambda subject, documents, **kwargs: {
        "version": "test", "transient": True, "subject_document_count": 1,
        "usable_document_count": 1, "blocked_documents": [], "scenarios": [{
            "standard_name": "일반암", "risk_kind": "critical_illness", "status": "no_gap",
            "runway_months": 4, "confirmed_diagnosis_won": 10_000_000,
            "runway_money_won": 12_000_000, "required_won": 18_000_000,
            "available_won": 12_000_000, "gap_won": 0, "items": [], "scope": "test",
        }], "result_summary": {
            "headline": "확인된 진단비와 비상자금으로 필수생활비를 약 4개월 감당할 수 있습니다.",
            "runway_label": "약 4개월", "critical_count": 1,
            "critical_scenarios": [{
                "standard_name": "일반암", "runway_months": 4,
                "confirmed_diagnosis_won": 10_000_000,
            }],
        }, "documents": [{"id": document_id, "name": "형식검증.pdf"}],
        "coverage_checks": [], "protection_statuses": [], "fallback": {}, "scope": "test",
    })
    form = client.get("/mock-analysis")
    assert form.status_code == 200
    assert form.headers["cache-control"] == "no-store"
    assert 'name="hospitalization_months"' not in form.text
    assert '네 가지만 알려주세요' in form.text
    result = client.post("/mock-analysis", data=profile_values() | {"document_id": str(document_id)})
    assert result.status_code == 200
    assert "모의계산 · 입력값과 결과를 저장하지 않습니다" in result.text
    assert "저장하지 않은 모의계산 결과" not in result.text
    assert "<small>생활비 유지</small><strong>약 4개월</strong>" in result.text
    assert "확인된 진단비와 비상자금으로 필수생활비를 약 4개월 감당할 수 있습니다." in result.text
    assert result.text.count("일반암 진단비와 비상자금으로") == 0
    assert "현재 결과 기준 저장" not in result.text
    with factory() as session:
        assert session.query(AnalysisSubject).count() == 0
        assert session.query(FinancialProfile).count() == 0
