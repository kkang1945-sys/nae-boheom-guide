"""Mechanical PDF fixtures contain no insurance policy or personal information."""
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.documents import DocumentUploadError, file_sha256, store_uploaded_document
from insurance_app.models import DocumentPage, InsuranceDocument, CoverageRecord
from insurance_app.pdf_reader import PdfReadError, page_status, read_pdf


def pdf_bytes(*, encrypted=False, text='Evidence 12345'):
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
        page.set_rotation(90)
        pdf.new_page()
        if encrypted:
            return pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='secret')
        return pdf.tobytes()


def test_pdf_text_coordinates_rotation_and_source_unchanged(tmp_path):
    source = tmp_path / 'mechanical.pdf'
    source.write_bytes(pdf_bytes())
    digest = file_sha256(source)
    pages = read_pdf(source)
    assert len(pages) == 2
    assert pages[0].page_number == 1
    assert 'Evidence 12345' in pages[0].text
    assert pages[0].rotation == 90
    assert pages[0].words[0][4] == 'Evidence'
    assert pages[0].words[0][0] == 72
    assert pages[0].status == 'text_ready'
    assert pages[1].status == 'needs_ocr'
    assert file_sha256(source) == digest


@pytest.mark.parametrize('text,images,status', [
    ('', False, 'needs_ocr'), ('\ufffd12', False, 'needs_ocr'),
    ('---', False, 'needs_ocr'), ('Heading', True, 'mixed_review'),
    ('문자 100', False, 'text_ready'),
])
def test_page_triage(text, images, status):
    assert page_status(text, images) == status


@pytest.mark.parametrize('payload,message', [
    (b'%PDF-1.7\ninvalid', '읽을 수 없습니다'),
    (pdf_bytes(encrypted=True), '암호'),
])
def test_invalid_pdf_leaves_no_managed_files(tmp_path, payload, message):
    factory = create_session_factory(tmp_path / 'test.sqlite3')
    create_schema(factory)
    uploads = tmp_path / 'uploads'
    with pytest.raises(DocumentUploadError, match=message), factory.begin() as session:
        store_uploaded_document(session, BytesIO(payload), 'input.pdf', uploads, max_bytes=100_000)
    assert not list(uploads.iterdir())
    with factory() as session:
        assert list(session.scalars(select(InsuranceDocument))) == []


@pytest.fixture
def isolated_app(tmp_path, monkeypatch):
    import insurance_app.main as main
    from insurance_app.config import load_settings
    settings = load_settings(tmp_path)
    factory = create_session_factory(settings.database_path)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'session_factory', factory)
    with TestClient(main.app) as client:
        yield client, factory, settings


def test_upload_view_duplicate_and_delete_pdf(isolated_app):
    client, factory, settings = isolated_app
    payload = pdf_bytes()
    response = client.post('/documents', files={'file': ('input.pdf', payload, 'application/pdf')})
    assert response.status_code == 200
    assert '보장내용과 보장금액' in response.text
    assert 'input.pdf' in response.text
    home = client.get('/').text
    assert 'input.pdf' in home
    document_url = response.url.path
    page_url = document_url + '/pages/1'
    page = client.get(page_url)
    assert page.status_code == 200
    assert 'Evidence 12345' in page.text
    assert page.headers['cache-control'] == 'no-store'
    image = client.get(page_url + '/image')
    assert image.status_code == 200
    assert image.content.startswith(b'\x89PNG')
    assert image.headers['cache-control'] == 'no-store'
    assert client.get(document_url + '/pages/0').status_code == 404
    assert client.get(document_url + '/pages/99/image').status_code == 404
    assert client.post('/documents', files={'file': ('copy.pdf', payload)}).url.path == document_url
    with factory() as session:
        assert len(list(session.scalars(select(DocumentPage)))) == 2
        assert len(list(session.scalars(select(InsuranceDocument)))) == 1
    assert len(list(settings.upload_dir.iterdir())) == 1
    assert client.post(document_url + '/delete', data={'confirm_delete': 'DELETE'}).status_code == 200
    assert client.get(page_url + '/image').status_code == 404
    assert not list(settings.upload_dir.iterdir())
    with factory() as session:
        assert not list(session.scalars(select(DocumentPage)))


def test_upload_multiple_documents_and_show_each_filename(isolated_app):
    client, factory, _ = isolated_app
    response = client.post('/documents', files=[
        ('file', ('first-policy.pdf', pdf_bytes(text='First policy'), 'application/pdf')),
        ('file', ('second-policy.pdf', pdf_bytes(text='Second policy'), 'application/pdf')),
    ])
    assert response.status_code == 200
    assert response.url.path == '/documents/combined'
    assert '내 보험증권 2개를 함께 봅니다' not in response.text
    assert '각 증권의 읽기 상태' not in response.text
    assert '찾은 보장' not in response.text
    assert '확인 완료' not in response.text
    assert '이 증권 자세히 보기' not in response.text
    assert '주요 보장내용' not in response.text  # empty synthetic PDFs stay concise
    assert '2개의 보험증권을 읽었습니다.' in response.text
    assert 'first-policy.pdf' in response.text
    assert 'second-policy.pdf' in response.text
    with factory() as session:
        assert len(list(session.scalars(select(InsuranceDocument)))) == 2


def test_multiple_upload_reports_encrypted_file_without_hiding_success(isolated_app):
    client, factory, _ = isolated_app
    response = client.post('/documents', files=[
        ('file', ('readable.pdf', pdf_bytes(text='Readable policy'), 'application/pdf')),
        ('file', ('locked.pdf', pdf_bytes(encrypted=True), 'application/pdf')),
    ])
    assert response.status_code == 200
    assert response.url.path == '/documents/combined'
    assert '1개의 보험증권을 읽었습니다.' in response.text
    assert '읽지 못한 파일' in response.text
    assert 'locked.pdf' in response.text
    assert '암호' in response.text
    assert 'readable.pdf' in response.text
    with factory() as session:
        assert len(list(session.scalars(select(InsuranceDocument)))) == 1


def test_changed_original_cannot_masquerade_as_evidence(isolated_app):
    client, factory, _ = isolated_app
    response = client.post('/documents', files={'file': ('input.pdf', pdf_bytes())})
    with factory() as session:
        document = session.scalar(select(InsuranceDocument))
        source = Path(document.source_path)
    source.write_bytes(b'changed')
    assert client.get(response.url.path + '/pages/1/image').status_code == 409
    source.unlink()
    assert client.get(response.url.path + '/pages/1/image').status_code == 410


def test_confirmation_wrong_document_does_not_change_coverage(isolated_app, tmp_path):
    from test_documents import _sample
    from insurance_app.documents import register_hwpml
    client, factory, _ = isolated_app
    with factory.begin() as session:
        document = register_hwpml(session, _sample(tmp_path / 'source.hwp'))
        coverage_id = document.coverages[0].id
    response = client.post(f'/coverages/{coverage_id}/confirm', data={
        'document_id': document.id + 1, 'name': 'test', 'amount_won': 1, 'period': 'test',
    })
    assert response.status_code == 404
    with factory() as session:
        coverage = session.get(CoverageRecord, coverage_id)
        assert coverage.review_status == 'needs_review'
        assert coverage.confirmed_name is None


def test_pdf_migration_preserves_existing_tables(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text
    database = tmp_path / 'migration.sqlite3'
    monkeypatch.setenv('INSURANCE_DATABASE_URL', f'sqlite:///{database.as_posix()}')
    config = Config('alembic.ini')
    command.upgrade(config, '20260902_0001')
    engine = create_engine(f'sqlite:///{database.as_posix()}')
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO insurance_documents (source_name, source_path, sha256, file_size, format, created_at) VALUES ('test', 'test', 'hash', 1, 'hwpml', CURRENT_TIMESTAMP)"))
    command.upgrade(config, 'head')
    assert 'document_pages' in inspect(engine).get_table_names()
    with engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM insurance_documents')).scalar() == 1
    engine.dispose()
