from io import BytesIO
from types import SimpleNamespace

from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.documents import delete_document_data, get_document, store_uploaded_document
from insurance_app.models import PolicyConditionEvidence, PolicyConditionEvidenceRevision
from insurance_app.policy_conditions import (
    _is_non_coverage_condition_line,
    _condition_scope_hint,
    auto_check_coverage_evidence,
    auto_check_document_scope,
    categories_for_text,
    clauses,
    condition_readiness,
    replace_machine_evidence,
    save_document_condition_reviews,
)
from test_pdf_audit import grid_pdf
from test_pdf import isolated_app


def test_only_explicit_condition_words_are_classified():
    assert categories_for_text("암으로 진단확정 시 가입금액 지급(최초 1회에 한함)") == [
        "payment_limit", "payment_trigger"
    ]
    assert categories_for_text("계약일부터 90일 이후 보장개시일, 1년 미만 50% 감액") == [
        "reduction", "waiting_period"
    ]
    assert categories_for_text("보험금을 지급하지 않는 사유") == ["exclusion", "payment_trigger"]
    assert categories_for_text("의미를 추측할 수 없는 설명") == []
    assert clauses("진단확정 시 지급.\n최초 1회에 한함") == ["진단확정 시 지급.", "최초 1회에 한함"]


def test_contract_administration_lines_are_not_benefit_condition_candidates():
    assert _is_non_coverage_condition_line("보장내용 상세(지급조건)")
    assert _is_non_coverage_condition_line("예상만기환급금은 중도인출에 따라 달라집니다.")
    assert _is_non_coverage_condition_line("청약의 철회를 위해 서류를 제출합니다.")
    assert not _is_non_coverage_condition_line(
        "과거의 질병을 알리지 않으면 보험금을 받지 못할 수 있습니다."
    )


def test_condition_scope_hints_do_not_confirm_the_scope():
    assert _condition_scope_hint("실손의료비 특약은 매년 갱신됩니다.") == "실손의료비 담보"
    assert _condition_scope_hint("간병인지원관련 특약 갱신계약") == "간병인지원 관련 특약"
    assert _condition_scope_hint("최초가입 후 특약을 추가한 경우") == "최초가입 뒤 추가·변경한 특약"
    assert _condition_scope_hint("일반적인 안내") is None


def test_upload_stores_source_linked_evidence_and_keeps_analysis_blocked(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(grid_pdf()), "conditions.pdf", tmp_path / "uploads", max_bytes=1_000_000
        )
        document_id = document.id
        assert len(document.pdf_coverages) == 2
        assert all(item.source_kind == "coverage_description_cell" for item in document.condition_evidence)
        assert all(item.source_bbox for item in document.condition_evidence)
        assert {item.category for item in document.condition_evidence} == {"benefit_text", "payment_trigger"}
        report = condition_readiness(document)
        assert report["evidence_count"] == 4
        assert not report["missing_benefit_coverage_ids"]
        assert report["unconfirmed_evidence_ids"]
        assert not report["analysis_ready"]
        assert any("표 밖 조건" in blocker for blocker in report["blockers"])
    with factory.begin() as session:
        document = get_document(session, document_id)
        before = len(document.condition_evidence)
        replace_machine_evidence(document)
        session.flush()
        assert len(document.condition_evidence) == before
        delete_document_data(session, document_id, tmp_path / "uploads", delete_managed_original=True)
    with factory() as session:
        assert list(session.scalars(select(PolicyConditionEvidence))) == []


def test_missing_description_is_an_analysis_blocker(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(grid_pdf()), "missing.pdf", tmp_path / "uploads", max_bytes=1_000_000
        )
        document.pdf_coverages[0].extracted_fields = document.pdf_coverages[0].extracted_fields | {"description": ""}
        replace_machine_evidence(document)
        report = condition_readiness(document)
        assert document.pdf_coverages[0].id in report["missing_benefit_coverage_ids"]
        assert not report["analysis_ready"]


def test_condition_screen_requires_current_transcription_audit(isolated_app):
    client, factory, _ = isolated_app
    response = client.post('/documents', files={'file': ('conditions.pdf', grid_pdf(), 'application/pdf')})
    assert response.status_code == 200
    with factory() as session:
        document_id = session.scalar(select(PolicyConditionEvidence)).document_id
    page = client.get(f'/documents/{document_id}/conditions')
    assert page.status_code == 200
    assert '자동 분류는 보험금 지급 여부나 보장 확정을 뜻하지 않습니다' in page.text
    assert '자동 근거 확인' in page.text
    with factory.begin() as session:
        document = get_document(session, document_id)
        document.pdf_coverages[0].extracted_fields = document.pdf_coverages[0].extracted_fields | {'amount_won': 7}
    assert client.get(f'/documents/{document_id}/conditions').status_code == 409


def test_only_independently_passed_coverage_cells_become_auto_checked(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(grid_pdf()), "auto-check.pdf", tmp_path / "uploads", max_bytes=1_000_000
        )
        rows = {str(row.id): {"passed": True} for row in document.pdf_coverages}
        assert auto_check_coverage_evidence(document, {"summary_ready": True, "rows": rows}) == 4
        assert all(item.review_status == "auto_checked" for item in document.condition_evidence
                   if item.coverage_id is not None)
        assert all(item.review_status != "auto_checked" for item in document.condition_evidence
                   if item.coverage_id is None)


def test_document_condition_scope_decision_keeps_revision(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(grid_pdf()), "scope.pdf", tmp_path / "uploads", max_bytes=1_000_000
        )
        item = PolicyConditionEvidence(page_number=1, coverage_id=None, category="exclusion",
            source_text="공통 조건 후보", source_bbox=[1, 1, 2, 2], source_kind="document_page_line_candidate",
            source_fingerprint="b" * 64, extracted_detail={"scope": "document_candidate"},
            review_status="machine_extracted", extractor_version="test")
        document.condition_evidence.append(item)
        session.flush()
        assert save_document_condition_reviews(session, document, {item.id: "coverage_specific"}) == 1
        assert item.review_status == "coverage_specific"
        assert item.revisions[0].before_status == "machine_extracted"
        assert not condition_readiness(document)["analysis_ready"]
        assert save_document_condition_reviews(session, document, {item.id: "not_applicable"}) == 1
        assert item.review_status == "not_applicable"
        assert save_document_condition_reviews(session, document, {item.id: "not_applicable"}) == 0
    with factory.begin() as session:
        delete_document_data(session, document.id, tmp_path / "uploads", delete_managed_original=True)
    with factory() as session:
        assert list(session.scalars(select(PolicyConditionEvidenceRevision))) == []


def test_document_scope_auto_check_requires_explicit_heading_and_position():
    words = [[10, 10, 100, 20, "공통", 1, 1, 1], [105, 10, 200, 20, "추가정보", 1, 1, 2],
             [205, 10, 250, 20, "사항", 1, 1, 3]]
    below = SimpleNamespace(coverage_id=None, review_status="machine_extracted", page_number=1,
        source_bbox=[10, 30, 200, 40], extracted_detail={})
    above = SimpleNamespace(coverage_id=None, review_status="machine_extracted", page_number=1,
        source_bbox=[10, 1, 200, 8], extracted_detail={})
    document = SimpleNamespace(pages=[SimpleNamespace(page_number=1, words=words)],
                               condition_evidence=[below, above])
    assert auto_check_document_scope(document) == 1
    assert below.review_status == "auto_checked"
    assert below.extracted_detail["scope"] == "document_common"
    assert above.review_status == "machine_extracted"
