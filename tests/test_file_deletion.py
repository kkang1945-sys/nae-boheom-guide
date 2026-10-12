from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.documents import store_uploaded_document, delete_document_data
from insurance_app.file_deletion import recover_deletions
from insurance_app.models import InsuranceDocument
from test_pdf import pdf_bytes


def stored(tmp_path):
    factory = create_session_factory(tmp_path / 'test.sqlite3')
    create_schema(factory)
    uploads = tmp_path / 'uploads'
    with factory.begin() as session:
        document = store_uploaded_document(session, BytesIO(pdf_bytes()), 'mechanical.pdf', uploads, max_bytes=100000)
        return factory, uploads, document.id, Path(document.source_path)


def test_rollback_keeps_original_and_database(tmp_path):
    factory, uploads, document_id, source = stored(tmp_path)
    original = source.read_bytes()
    with pytest.raises(RuntimeError), factory.begin() as session:
        delete_document_data(session, document_id, uploads, delete_managed_original=True)
        assert source.read_bytes() == original
        raise RuntimeError('simulated commit cancellation')
    with factory() as session:
        assert session.get(InsuranceDocument, document_id)
    assert source.read_bytes() == original
    assert not list((tmp_path / 'deletion-journal').glob('*.json'))


def test_locked_file_retries_after_database_commit(tmp_path, monkeypatch):
    factory, uploads, document_id, source = stored(tmp_path)
    original_unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == source:
            raise PermissionError('simulated file lock')
        return original_unlink(path, *args, **kwargs)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, 'unlink', locked)
        with factory.begin() as session:
            delete_document_data(session, document_id, uploads, delete_managed_original=True)
        assert session.info['deletion_pending'] == 1
    with factory() as session:
        assert not session.get(InsuranceDocument, document_id)
    assert source.exists()
    assert recover_deletions(uploads, factory.kw['bind']) == 0
    assert not source.exists()


def test_interrupted_uncommitted_job_preserves_referenced_source(tmp_path):
    factory, uploads, document_id, source = stored(tmp_path)
    from insurance_app.file_deletion import stage_file_deletion
    # A persisted journal can precede the DB transaction in a process interruption.
    with factory() as session:
        stage_file_deletion(session, source, uploads)
    assert recover_deletions(uploads, factory.kw['bind']) == 0
    assert source.exists()
