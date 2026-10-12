from insurance_app.analysis_preflight import analysis_preflight
from insurance_app.documents import get_document
from insurance_app.financial_profiles import save_financial_profile
from insurance_app.models import PolicyConditionEvidence
from insurance_app.pdf_audit import audit_document
from insurance_app.subjects import save_subject
from test_pdf_audit import audited


def test_preflight_lists_blockers_instead_of_calculating(audited):
    factory, document_id, _ = audited
    with factory() as session:
        report = analysis_preflight(get_document(session, document_id))
    assert not report["analysis_ready"]
    failed = {item["key"] for item in report["checks"] if not item["passed"]}
    assert {"subject", "metadata", "condition_review", "semantics"} <= failed
    assert "지급 가능성을 판단하지 않습니다" in report["scope"]


def test_preflight_can_pass_only_when_every_gate_is_satisfied(audited):
    factory, document_id, _ = audited
    with factory.begin() as session:
        document = get_document(session, document_id)
        document.subject = save_subject(session, label="본인", relationship_to_user="self",
            birth_year=1990, life_stage="working", economic_role="primary_earner",
            sex_specific_review="both", pregnancy_childbirth_review="not_applicable")
        document.confirmed_insurer = "확인 보험회사"
        document.confirmed_product = "확인 상품"
        document.metadata_review_status = "confirmed"
        for row in document.pdf_coverages:
            row.confirmed_fields = row.extracted_fields | {
                "renewal": "nonrenewable", "contract_role": "rider",
                "compensation_type": "fixed", "payment_frequency": "once",
            }
        document.condition_evidence.append(PolicyConditionEvidence(
            page_number=1, coverage_id=None, category="payment_limit", source_text="공통 조건 확인용",
            source_bbox=[1, 1, 2, 2], source_kind="document_page_line_candidate",
            source_fingerprint="a" * 64, extracted_detail={"scope": "document_candidate"},
            review_status="confirmed", extractor_version="test"))
        for item in document.condition_evidence:
            item.review_status = "confirmed"
        audit_document(session, document)
        report = analysis_preflight(document)
        assert report["analysis_ready"], report


def test_preflight_requires_life_stage_inputs(audited):
    factory, document_id, _ = audited
    with factory.begin() as session:
        document = get_document(session, document_id)
        document.subject = save_subject(session, label="검증", relationship_to_user="child",
            birth_year=2020, life_stage="child", economic_role="dependent",
            sex_specific_review="both", pregnancy_childbirth_review="not_applicable")
        report = analysis_preflight(document)
        gate = next(item for item in report['checks'] if item['key'] == 'life_stage')
        assert not gate['passed'] and '돌봄비' in gate['message']
        document.subject.life_stage = 'retired_older_adult'
        gate = next(item for item in analysis_preflight(document)['checks'] if item['key'] == 'life_stage')
        assert not gate['passed'] and '지속소득' in gate['message']


def test_preflight_life_stage_gate_passes_after_explicit_child_inputs(audited):
    factory, document_id, _ = audited
    with factory.begin() as session:
        document = get_document(session, document_id)
        document.subject = save_subject(session, label="검증", relationship_to_user="child",
            birth_year=2020, life_stage="child", economic_role="dependent",
            sex_specific_review="both", pregnancy_childbirth_review="not_applicable")
        save_financial_profile(session, document.subject, {
            "income_gap_months": "6", "emergency_fund_to_use_won": "0",
            "guardian_monthly_income_loss_won": "1000000",
            "child_monthly_care_cost_won": "0", "paid_leave_support_won": "0",
        })
        gate = next(item for item in analysis_preflight(document)['checks'] if item['key'] == 'life_stage')
        assert gate['passed']
