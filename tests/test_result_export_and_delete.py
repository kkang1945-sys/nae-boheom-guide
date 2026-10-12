import csv
from io import StringIO
from types import SimpleNamespace
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient
from sqlalchemy import select

from insurance_app import models
from insurance_app.result_export import result_csv, result_pdf
from insurance_app.result_snapshots import save_result_snapshot
from insurance_app.subjects import save_subject
from insurance_app.financial_profiles import save_financial_profile
from test_financial_profiles import subject_values, profile_values
from test_pdf import isolated_app, pdf_bytes


def test_csv_keeps_unknown_blank_and_escapes_spreadsheet_formula():
    report = {"version": "v1", "scenarios": [{"standard_name": "일반암", "status": "pending", "missing": ["생활비"]}]}
    rows = list(csv.reader(StringIO(result_csv(SimpleNamespace(label='=1+1'), report).lstrip('\ufeff'))))
    assert rows[1][0] == "'=1+1"
    assert rows[1][3:6] == ['', '', '']
    assert rows[1][7] == '생활비'
    assert rows[0][-2:] == ['검토 필요 규모(원)', '핵심 안내']


def test_csv_masks_a_korean_subject_name():
    report = {"scenarios": [], "version": "test", "fallback": {}}
    exported = result_csv(SimpleNamespace(label="홍길동"), report)
    assert "홍길동" not in exported
    assert "홍**" in exported


def test_saved_export_uses_frozen_inputs_and_does_not_save_on_download(isolated_app):
    client, factory, _ = isolated_app
    with factory.begin() as session:
        subject = save_subject(session, **subject_values())
        save_financial_profile(session, subject, profile_values())
        subject_id = subject.id
    client.post(f'/subjects/{subject_id}/results/snapshot')
    with factory.begin() as session:
        snapshot = session.scalar(select(models.AnalysisResultSnapshot))
        snapshot_id = snapshot.id
        subject = session.get(models.AnalysisSubject, subject_id)
        subject.label = '변경한이름'
        save_financial_profile(session, subject, profile_values(monthly_essential_expenses_won='7000000'))
    old = client.get(f'/results/snapshots/{snapshot_id}/export.pdf')
    current = client.get(f'/subjects/{subject_id}/results/export.pdf')
    assert old.status_code == current.status_code == 200
    assert old.headers['cache-control'] == 'no-store'
    with pymupdf.open(stream=old.content) as pdf:
        text = ''.join(page.get_text() for page in pdf)
        assert '3,000,000원' in text and '7,000,000원' not in text
        assert '변경한이름' not in text and '판단 보류' in text
    with pymupdf.open(stream=current.content) as pdf:
        assert '7,000,000원' in ''.join(page.get_text() for page in pdf)
    page = client.get(f'/results/snapshots/{snapshot_id}').text
    assert f'/subjects/{subject_id}/results/export' not in page
    with factory() as session:
        assert session.query(models.AnalysisResultSnapshot).count() == 1
    assert client.get('/subjects/99999/results/export.pdf').status_code == 404
    assert client.get('/results/snapshots/99999/export.pdf').status_code == 404


def test_full_delete_clears_dependents_preserves_external_and_stays_deleted(isolated_app):
    client, factory, settings = isolated_app
    import insurance_app.main as main
    from insurance_app.documents import register_hwpml
    from test_documents import _sample
    external = _sample(settings.project_root / '현대해상 보험 증권.hwp')
    original = external.read_bytes()
    client.post('/documents', files={'file': ('mechanical.pdf', pdf_bytes())})
    with factory.begin() as session:
        register_hwpml(session, external)
        subject = save_subject(session, **subject_values())
        save_financial_profile(session, subject, profile_values())
        save_result_snapshot(session, subject, {'version': 'test', 'scenarios': []})
    assert client.post('/data/delete', data={'confirm_delete': 'wrong'}).status_code == 422
    with factory() as session:
        assert session.query(models.InsuranceDocument).count() == 2
    assert client.post('/data/delete', data={'confirm_delete': 'DELETE_ALL'}).status_code == 200
    assert external.read_bytes() == original
    assert not list(settings.upload_dir.glob('*'))
    with TestClient(main.app):
        with factory() as session:
            for table in models.Base.metadata.sorted_tables:
                assert not session.execute(select(table)).first(), table.name


def test_pdf_long_content_and_zero_remain_readable(tmp_path):
    report = {'version': 'test-v1', 'scenarios': [
        {'standard_name': '일반암', 'status': 'no_gap', 'required_won': 0, 'available_won': 0, 'gap_won': 0,
         'items': [{'document_name': '원문이름' * 70, 'page_number': 7, 'coverage_name': '진단비', 'amount_won': 0}]}
    ], 'blocked_documents': []}
    content = result_pdf('검증용 대상자', report)
    with pymupdf.open(stream=content) as pdf:
        text = ''.join(page.get_text() for page in pdf)
        assert '추정 자금공백 0원' in text
        assert '가정과 한계' in text
        assert '\ufffd' not in text
        for page in pdf:
            for word in page.get_text('words'):
                assert 0 <= word[0] < word[2] <= page.rect.width
                assert 0 <= word[1] < word[3] <= page.rect.height
