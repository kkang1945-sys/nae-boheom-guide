from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.documents import delete_document_data, get_document, store_uploaded_document
from insurance_app.models import OcrPageResult
from insurance_app.ocr import OcrError, _parse_tsv, current_ocr, ocr_page
from test_pdf import pdf_bytes


def test_tsv_words_are_mapped_back_to_pdf_coordinates():
    tsv = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    tsv += "5\t1\t1\t1\t1\t1\t100\t200\t300\t100\t91.5\t보험\n"
    text, words = _parse_tsv(tsv, 600, 800, 1200, 1600)
    assert text == "보험"
    assert words == [[50, 100, 200, 150, "보험", 91.5]]


def test_tsv_parser_does_not_treat_an_ocr_quote_as_csv_quoting():
    header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
    tsv = header
    tsv += '5\t1\t1\t1\t1\t1\t10\t10\t10\t10\t80\t"\n'
    tsv += "5\t1\t1\t1\t1\t2\t30\t10\t40\t10\t95\t공시이율\n"

    text, words = _parse_tsv(tsv, 100, 100, 100, 100)

    assert "공시이율" in text
    assert words[-1][4] == "공시이율"


def test_ocr_is_source_bound_cached_retryable_and_deleted(monkeypatch, tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    executable = tmp_path / "tesseract.exe"
    executable.write_bytes(b"fake")
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "kor.traineddata").write_bytes(b"fake")
    monkeypatch.setattr("insurance_app.ocr.engine_version", lambda _: "tesseract-test")
    monkeypatch.setattr("insurance_app.ocr.recognize_page", lambda *args, **kwargs: {
        "text": "로컬 OCR 결과", "words": [[1, 2, 3, 4, "결과", 99]], "status": "ocr_ready",
        "engine_version": "tesseract-test", "languages": "kor+eng", "dpi": 300,
    })
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(pdf_bytes()), "ocr.pdf", tmp_path / "uploads", max_bytes=100_000
        )
        document_id = document.id
        first = ocr_page(session, document, 2, executable, tessdata)
        assert ocr_page(session, document, 2, executable, tessdata).id == first.id
        retry = ocr_page(session, document, 2, executable, tessdata, force=True)
        assert retry.id != first.id
        assert current_ocr(document, 2).text == "로컬 OCR 결과"
    with factory.begin() as session:
        document = get_document(session, document_id)
        Path(document.source_path).write_bytes(b"changed")
        with pytest.raises(OcrError, match="변경"):
            ocr_page(session, document, 2, executable, tessdata)
        Path(document.source_path).unlink()
        delete_document_data(session, document_id, tmp_path / "uploads", delete_managed_original=True)
    with factory() as session:
        assert list(session.scalars(select(OcrPageResult))) == []


def test_missing_local_korean_data_fails_closed(tmp_path):
    with pytest.raises(OcrError, match="한국어"):
        from insurance_app.ocr import recognize_page
        recognize_page(tmp_path / "missing.pdf", 1, tmp_path / "missing.exe", tmp_path / "tessdata")
