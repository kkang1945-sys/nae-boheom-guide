"""Commit-aware managed-file deletion with a durable retry journal."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from insurance_app.models import InsuranceDocument


def _journal_dir(upload_dir):
    return Path(upload_dir).resolve().parent / 'deletion-journal'


def _process(journal, upload_dir, engine):
    """Delete only an unreferenced, unchanged file directly inside managed uploads."""
    try:
        entry = json.loads(journal.read_text(encoding='utf-8'))
        name = entry['name']
        if Path(name).name != name or name in {'', '.', '..'}:
            return False
        root = Path(upload_dir).resolve()
        source = root / name
        if source.resolve().parent != root:
            return False
        with engine.connect() as connection:
            paths = connection.execute(select(InsuranceDocument.source_path)).scalars()
            if any(Path(path).resolve() == source.resolve() for path in paths):
                journal.unlink(missing_ok=True)
                return True
        if source.exists():
            if hashlib.sha256(source.read_bytes()).hexdigest() != entry['sha256']:
                return False
            source.unlink()
        journal.unlink(missing_ok=True)
        return True
    except (OSError, ValueError, KeyError):
        return False


def recover_deletions(upload_dir, engine):
    folder = _journal_dir(upload_dir)
    if not folder.exists():
        return 0
    return sum(not _process(journal, upload_dir, engine) for journal in folder.glob('*.json'))


def stage_file_deletion(session, source, upload_dir):
    source, root = Path(source).resolve(), Path(upload_dir).resolve()
    if source.parent != root:
        raise ValueError('관리 업로드 폴더 바로 아래 파일만 삭제할 수 있습니다.')
    if not source.exists():
        return
    folder = _journal_dir(root)
    folder.mkdir(parents=True, exist_ok=True)
    journal = folder / f'{uuid4().hex}.json'
    payload = {'name': source.name, 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    with journal.open('x', encoding='utf-8') as stream:
        json.dump(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    session.info.setdefault('file_deletions', []).append((journal, root))


@event.listens_for(Session, 'after_commit')
def _after_commit(session):
    jobs = session.info.pop('file_deletions', [])
    session.info['deletion_pending'] = sum(not _process(journal, root, session.get_bind()) for journal, root in jobs)


@event.listens_for(Session, 'after_rollback')
def _after_rollback(session):
    for journal, _ in session.info.pop('file_deletions', []):
        journal.unlink(missing_ok=True)
