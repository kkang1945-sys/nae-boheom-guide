from pathlib import Path

from insurance_app.database import Base, create_schema, create_session_factory
from insurance_app.documents import (
    DocumentUploadError,
    confirm_coverage,
    delete_document_data,
    register_hwpml,
    store_uploaded_document,
)
from insurance_app import models  # noqa: F401


def _sample(path: Path) -> Path:
    path.write_text(
        """<HWPML><BODY>
        <P><TEXT><CHAR>안심보험(Hi1234)기본형</CHAR></TEXT></P>
        <TABLE><ROW>
          <CELL><P><TEXT><CHAR>암진단담보</CHAR></TEXT></P></CELL>
          <CELL><P><TEXT><CHAR>2천만원</CHAR></TEXT></P></CELL>
          <CELL><P><TEXT><CHAR>3년만기갱신</CHAR></TEXT></P></CELL>
          <CELL><P><TEXT><CHAR>진단 확정 시 지급</CHAR></TEXT></P></CELL>
        </ROW></TABLE></BODY></HWPML>""",
        encoding="utf-8",
    )
    return path


def test_register_is_idempotent_and_keeps_values_unconfirmed(tmp_path: Path) -> None:
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    source = _sample(tmp_path / "현대해상 증권.hwp")

    with factory.begin() as session:
        first = register_hwpml(session, source)
        first_id = first.id
    with factory.begin() as session:
        second = register_hwpml(session, source)
        assert second.id == first_id
        assert len(second.coverages) == 1
        assert second.coverages[0].review_status == "needs_review"
        assert second.coverages[0].confirmed_amount_won is None


def test_confirm_preserves_extracted_value(tmp_path: Path) -> None:
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    source = _sample(tmp_path / "현대해상 증권.hwp")

    with factory.begin() as session:
        document = register_hwpml(session, source)
        coverage_id = document.coverages[0].id
        original = document.coverages[0].extracted_amount_won
        confirm_coverage(
            session,
            coverage_id,
            name="사용자가 확인한 암진단담보",
            amount_won=19_000_000,
            period="사용자가 확인한 기간",
        )

    with factory() as session:
        coverage = session.get(models.CoverageRecord, coverage_id)
        assert coverage is not None
        assert coverage.extracted_amount_won == original
        assert coverage.confirmed_amount_won == 19_000_000
        assert coverage.review_status == "confirmed"


def test_upload_stores_managed_copy_and_delete_removes_it(tmp_path: Path) -> None:
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    source = _sample(tmp_path / "source.hwp")
    upload_dir = tmp_path / "uploads"

    with source.open("rb") as stream, factory.begin() as session:
        document = store_uploaded_document(session, stream, "보험증권.hwp", upload_dir, max_bytes=1_000_000)
        document_id = document.id
        stored_path = Path(document.source_path)
    assert stored_path.exists()
    assert stored_path.parent == upload_dir

    with factory.begin() as session:
        assert delete_document_data(session, document_id, upload_dir, delete_managed_original=True)
    assert not stored_path.exists()


def test_upload_rejects_fake_pdf(tmp_path: Path) -> None:
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    fake = tmp_path / "fake.pdf"
    fake.write_text("not a pdf", encoding="utf-8")

    with fake.open("rb") as stream, factory.begin() as session:
        try:
            store_uploaded_document(session, stream, "fake.pdf", tmp_path / "uploads", max_bytes=100)
        except DocumentUploadError as exc:
            assert "실제 PDF" in str(exc)
        else:
            raise AssertionError("가짜 PDF가 거부되지 않았습니다.")
