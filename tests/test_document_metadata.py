from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.document_metadata import (
    _insurer_from_ocr,
    _product_from_text,
    confirm_document_metadata,
    detect_metadata_candidates,
)
from insurance_app.documents import delete_document_data, get_document, store_uploaded_document
from insurance_app.models import DocumentMetadataRevision, OcrPageResult
from test_pdf import pdf_bytes, isolated_app


def add_document(factory, upload_dir):
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(pdf_bytes()), "metadata.pdf", upload_dir, max_bytes=100_000
        )
        document.product_candidate = "안심보험(Hi1234)기본형"
        document.ocr_results.append(OcrPageResult(
            page_number=1, source_sha256=document.sha256, input_fingerprint="f" * 64,
            engine_version="test", languages="kor+eng", dpi=300,
            text="현대해상화재보험", words=[], status="ocr_ready", error_message=None))
        detect_metadata_candidates(document)
        session.flush()
        return document.id


def test_candidates_confirmation_revision_and_delete_are_separate(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    document_id = add_document(factory, tmp_path / "uploads")
    with factory.begin() as session:
        document = get_document(session, document_id)
        assert document.insurer_candidate == "현대해상화재보험"
        assert document.confirmed_insurer is None
        confirm_document_metadata(session, document, insurer="사용자 확인 보험회사", product="사용자 확인 상품")
        assert document.insurer_candidate == "현대해상화재보험"
        assert document.confirmed_insurer == "사용자 확인 보험회사"
        assert len(document.metadata_revisions) == 1
        confirm_document_metadata(session, document, insurer="사용자 확인 보험회사", product="사용자 확인 상품")
        assert len(document.metadata_revisions) == 1
    with factory.begin() as session:
        delete_document_data(session, document_id, tmp_path / "uploads", delete_managed_original=True)
    with factory() as session:
        assert list(session.scalars(select(DocumentMetadataRevision))) == []


def test_changed_source_blocks_metadata_confirmation(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    document_id = add_document(factory, tmp_path / "uploads")
    with pytest.raises(ValueError, match="변경"), factory.begin() as session:
        document = get_document(session, document_id)
        Path(document.source_path).write_bytes(b"changed")
        confirm_document_metadata(session, document, insurer="보험회사", product="상품")


def test_metadata_route_requires_source_check(isolated_app):
    client, factory, settings = isolated_app
    document_id = add_document(factory, settings.upload_dir)
    url = f'/documents/{document_id}/metadata'
    values = {'insurer': '확인 보험회사', 'product': '확인 상품'}
    assert client.post(url, data=values).status_code == 422
    assert client.post(url, data=values | {'source_checked': 'yes'}).status_code == 200
    with factory() as session:
        document = get_document(session, document_id)
        assert document.metadata_review_status == 'confirmed'


def test_insurer_candidate_tolerates_spaces_inserted_by_ocr():
    assert _insurer_from_ocr("보험회사: 한 화 손 해 보 험") == "한화손해보험"
    assert _insurer_from_ocr("보험금 지급 여부는 약관을 확인하세요") is None


def test_labeled_policy_issuer_wins_over_company_name_in_address():
    document = SimpleNamespace(
        pages=[
            SimpleNamespace(
                page_number=1,
                text="대전 서구 둔산동, 한화생명보험(주)\n무배당 알파Plus보장보험1304",
            ),
            SimpleNamespace(page_number=6, text="발행자 메리츠화재"),
        ],
        ocr_results=[
            SimpleNamespace(page_number=1, text="대전 서구 둔산동, 한 화 생 명 보 험 (주)"),
        ],
        insurer_candidate="한화생명보험",
        product_candidate=None,
        sha256="a" * 64,
        metadata_evidence=None,
    )

    evidence = detect_metadata_candidates(document)

    assert document.insurer_candidate == "메리츠화재"
    assert document.product_candidate == "무배당알파Plus보장보험1304"
    assert evidence["insurer"] == {
        "value": "메리츠화재",
        "page_number": 6,
        "source": "direct_pdf_text_label",
    }
    assert evidence["product"]["page_number"] == 1


def test_product_title_does_not_use_general_policy_sentences():
    assert _product_from_text("무배당 알파Plus보장보험1304") == "무배당알파Plus보장보험1304"
    assert _product_from_text("이 보험계약은 약관에서 정한 바에 따라 보장됩니다.") is None
