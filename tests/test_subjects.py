from io import BytesIO

import pytest
from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.documents import store_uploaded_document
from insurance_app.financial_profiles import save_financial_profile
from insurance_app.models import AnalysisResultSnapshot, AnalysisSubject, InsuranceDocument
from insurance_app.subjects import (
    assign_contract_group,
    assign_document,
    save_subject,
    subject_context,
    subject_readiness,
)
from test_pdf import pdf_bytes, isolated_app


def values(**changes):
    result = dict(label="첫째 아이", relationship_to_user="child", birth_year=2020,
                  life_stage="child", economic_role="dependent",
                  sex_specific_review="both", pregnancy_childbirth_review="not_applicable")
    return result | changes


def test_subject_keeps_demographics_out_of_recommended_amounts(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        subject = save_subject(session, **values())
        assert subject.review_status == "confirmed"
        assert not hasattr(subject, "recommended_amount_won")
        readiness = subject_readiness(subject)
        assert readiness["subject_ready"]
        assert not readiness["calculation_ready"]
        assert any("보호자" in item for item in readiness["blockers"])
        document = store_uploaded_document(
            session, BytesIO(pdf_bytes()), "child.pdf", tmp_path / "uploads", max_bytes=100_000
        )
        assign_document(session, document.id, subject.id)
        assert document.subject_id == subject.id


@pytest.mark.parametrize("change", [
    {"label": ""}, {"birth_year": 1800}, {"life_stage": "invalid"},
    {"economic_role": "unknown"}, {"sex_specific_review": "unknown"},
    {"pregnancy_childbirth_review": "unknown"},
])
def test_invalid_or_unconfirmed_subject_values_are_rejected(tmp_path, change):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with pytest.raises(ValueError), factory.begin() as session:
        save_subject(session, **values(**change))


def test_unassigned_document_is_explicitly_blocked():
    report = subject_readiness(None)
    assert not report["subject_ready"]
    assert not report["calculation_ready"]
    assert "지정되지 않았습니다" in report["blockers"][0]


def test_child_and_retired_subjects_become_ready_only_after_stage_inputs(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        child = save_subject(session, **values())
        save_financial_profile(session, child, {
            "income_gap_months": "6", "emergency_fund_to_use_won": "0",
            "guardian_monthly_income_loss_won": "0", "child_monthly_care_cost_won": "0",
            "paid_leave_support_won": "0",
        })
        assert subject_readiness(child)["calculation_ready"]

        retired = save_subject(session, **values(label="부모님", relationship_to_user="parent",
            birth_year=1950, life_stage="retired_older_adult", economic_role="retired"))
        save_financial_profile(session, retired, {
            "income_gap_months": "6", "emergency_fund_to_use_won": "0",
            "monthly_essential_expenses_won": "2000000",
            "retirement_monthly_income_to_use_won": "1000000",
            "retirement_monthly_care_cost_won": "0",
        })
        assert subject_readiness(retired)["calculation_ready"]


@pytest.mark.parametrize("life_stage,expected_label", [
    ("young_adult", "청년·경제활동 중"),
    ("middle_age", "중년"),
    ("retired_older_adult", "노년·은퇴기"),
])
def test_life_stage_context_selects_inputs_without_recommended_amounts(tmp_path, life_stage, expected_label):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    with factory.begin() as session:
        subject = save_subject(session, **values(life_stage=life_stage))
        context = subject_context(subject)
        assert context["stage_label"] == expected_label
        assert context["focus"]
        assert not hasattr(subject, "recommended_amount_won")


def test_subject_screen_create_and_assign_document(isolated_app):
    client, factory, settings = isolated_app
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(pdf_bytes()), "person.pdf", settings.upload_dir, max_bytes=100_000
        )
        document_id = document.id
    assert client.get('/subjects').status_code == 200
    response = client.post('/subjects', data={key: str(value) for key, value in values().items()})
    assert response.status_code == 200
    with factory() as session:
        subject = session.scalar(select(AnalysisSubject))
        assert subject.label == "첫째 아이"
        subject_id = subject.id
    subject_page = client.get('/subjects')
    assert '이 생애단계에서 확인할 내용' in subject_page.text
    assert '성별 관련 담보를 원문 그대로 모두 확인' in subject_page.text
    assert client.post(f'/documents/{document_id}/subject', data={'subject_id': subject_id}).status_code == 200
    integration = client.get(f'/subjects/{subject_id}/integration')
    assert integration.status_code == 200
    assert '현재 조건을 모두 만족해 합산할 수 있는 담보가 없습니다' in integration.text
    assert integration.headers['cache-control'] == 'no-store'
    results = client.get(f'/results?subject_id={subject_id}')
    assert results.status_code == 200
    assert '첫째 아이의 현재 결과' in results.text
    assert results.headers['cache-control'] == 'no-store'
    saved_snapshot = client.post(f'/subjects/{subject_id}/results/snapshot')
    assert saved_snapshot.status_code == 200
    assert '결과 기준을 저장했습니다' in saved_snapshot.text
    with factory() as session:
        snapshot_id = session.scalar(select(AnalysisResultSnapshot.id))
    reopened = client.get(f'/results/snapshots/{snapshot_id}')
    assert reopened.status_code == 200
    assert '저장된 결과' in reopened.text
    compared = client.get(f'/results/snapshots/{snapshot_id}/compare')
    assert compared.status_code == 200
    assert '저장 결과와 현재 결과 비교' in compared.text
    evidence = client.get(f'/subjects/{subject_id}/evidence')
    assert evidence.status_code == 200
    assert '결과 근거' in evidence.text
    assert evidence.headers['cache-control'] == 'no-store'
    with factory() as session:
        assert session.get(InsuranceDocument, document_id).subject_id == subject_id
    home = client.get('/')
    assert '첫째 아이' in home.text
    updated = values(label='은퇴한 부모님', relationship_to_user='parent', birth_year=1950,
                     life_stage='retired_older_adult', economic_role='retired',
                     sex_specific_review='female_related')
    assert client.post(f'/subjects/{subject_id}', data={key: str(value) for key, value in updated.items()}).status_code == 200
    with factory() as session:
        assert session.get(AnalysisSubject, subject_id).label == '은퇴한 부모님'
        assert session.get(InsuranceDocument, document_id).subject_id == subject_id
    assert client.post(f'/subjects/{subject_id}/delete', data={'confirm_delete': 'wrong'}).status_code == 422
    assert client.post(f'/subjects/{subject_id}/delete', data={'confirm_delete': 'DELETE'}).status_code == 200
    with factory() as session:
        assert session.get(AnalysisSubject, subject_id) is None
        assert session.get(InsuranceDocument, document_id).subject_id is None


def test_subject_edit_html_masks_likely_name_and_blank_keeps_it(isolated_app):
    client, factory, _ = isolated_app
    private_label = "홍길동"
    with factory.begin() as session:
        subject = save_subject(session, **values(label=private_label))
        subject_id = subject.id

    page = client.get('/subjects')
    assert private_label not in page.text
    assert "홍**" in page.text

    update = values(label="")
    assert client.post(
        f'/subjects/{subject_id}', data={key: str(value) for key, value in update.items()}
    ).status_code == 200
    with factory() as session:
        assert session.get(AnalysisSubject, subject_id).label == private_label


def _query_document(index, fingerprint=None):
    evidence = ({"query_layout": {"contract_group_fingerprint": fingerprint}}
                if fingerprint else None)
    return InsuranceDocument(
        source_name=f"query-{index}.pdf",
        source_path=f"query-{index}.pdf",
        sha256=f"{index:064x}",
        file_size=100,
        format="pdf",
        insurer_candidate="확인 보험회사",
        product_candidate="확인 상품",
        metadata_evidence=evidence,
    )


def test_assign_contract_group_moves_only_verified_group_members(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    fingerprint = "f" * 64
    with factory.begin() as session:
        subject = save_subject(session, **values(label="나", relationship_to_user="self",
                               life_stage="working", economic_role="primary_earner"))
        members = [_query_document(index, fingerprint) for index in range(1, 4)]
        standalone = _query_document(4)
        session.add_all([*members, standalone])
        session.flush()

        with pytest.raises(ValueError, match="묶음 전체"):
            assign_document(session, members[0].id, subject.id)
        assigned = assign_contract_group(session, members[1].id, subject.id)

        assert [document.id for document in assigned] == [document.id for document in members]
        assert all(document.subject_id == subject.id for document in members)
        assert standalone.subject_id is None
        with pytest.raises(ValueError, match="같은 계약"):
            assign_contract_group(session, standalone.id, subject.id)


def test_subject_screen_assigns_verified_query_documents_together(isolated_app):
    client, factory, _ = isolated_app
    fingerprint = "a" * 64
    with factory.begin() as session:
        subject = save_subject(session, **values(label="나", relationship_to_user="self",
                               life_stage="working", economic_role="primary_earner"))
        members = [_query_document(index, fingerprint) for index in range(11, 14)]
        standalone = _query_document(14)
        session.add_all([*members, standalone])
        session.flush()
        subject_id = subject.id
        representative_id = members[0].id
        standalone_id = standalone.id

    page = client.get("/subjects")
    assert page.status_code == 200
    assert "같은 계약 조회자료 3개" in page.text
    assert page.text.count("같은 계약 자료 모두 연결") == 1
    assert page.text.count("/subject-group") == 1
    assert client.post(
        f"/documents/{representative_id}/subject", data={"subject_id": subject_id}
    ).status_code == 422

    response = client.post(
        f"/documents/{representative_id}/subject-group", data={"subject_id": subject_id}
    )
    assert response.status_code == 200
    assert "같은 계약 조회자료 3개를 한 대상자에게 연결했습니다" in response.text
    with factory() as session:
        grouped = list(session.scalars(
            select(InsuranceDocument).where(InsuranceDocument.id != standalone_id)
        ))
        assert all(document.subject_id == subject_id for document in grouped)
        assert session.get(InsuranceDocument, standalone_id).subject_id is None

    assert client.post(
        f"/documents/{standalone_id}/subject-group", data={"subject_id": subject_id}
    ).status_code == 422
