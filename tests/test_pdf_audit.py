"""Anonymous mechanical table cells exercise actual independent PDF engines."""
from io import BytesIO
from pathlib import Path

import pymupdf
import pytest
from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.documents import (
    DocumentUploadError,
    store_uploaded_document,
    get_document,
    delete_document_data,
    reextract_pdf_document,
)
from insurance_app.models import OcrPageResult, PdfAudit
from insurance_app.ocr_policy_coverage import extract_kyobo_image_policy
import insurance_app.pdf_audit as audit_module
from insurance_app.pdf_audit import audit_document, current_audit, independent_won
from test_pdf import isolated_app


def test_source_row_count_falls_back_to_independent_amount_column(monkeypatch):
    rows = [type("Row", (), {"raw_cells": ["name", "amount", "period", "detail"]})()
            for _ in range(3)]
    monkeypatch.setattr(audit_module, "source_table_rows", lambda _: None)
    monkeypatch.setattr(audit_module, "source_amounts", lambda _: [object(), object(), object()])

    assert audit_module.independent_source_row_count(object(), rows) == 3


def grid_pdf():
    with pymupdf.open() as pdf:
        p=pdf.new_page()
        xs=[40,180,270,390,550]
        ys=[80,110,150,190]
        for x in xs:
            p.draw_line((x,ys[0]),(x,ys[-1]))
        for y in ys:
            p.draw_line((xs[0],y),(xs[-1],y))
        rows=[['담보명','가입금액','납기 및 만기','보장내용'],
              ['검사 A','25만원','20년납90세만기','가입금액 지급'],
              ['검사 B','30만원','10년납80세만기','가입금액 지급']]
        for y, cells in zip([100,135,175],rows):
            for x,text in zip([50,194,280,400],cells):
                p.insert_text((x,y),text,fontname='korea',fontsize=9)
        return pdf.tobytes()


def six_column_grid_pdf():
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=595, height=300)
        xs = [20, 82, 260, 330, 400, 475, 580]
        ys = [40, 75, 145, 220]
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        rows = [
            ['담보명', '', '보험시기', '보험종기', '가입금액\n(만원)', '보장내용 상세(지급조건)'],
            ['기본계약', '일반상해후유장해\n20년납 100세만기', '2013-06-26', '2086-06-26', '10,000', '가입금액 지급'],
            ['선택계약', '갱신형 상해통원의료비\n1년납 1년만기', '2026-06-26', '2027-06-26', '25', '공제금액 차감후 가입금액 한도로 보상'],
        ]
        for row_index, cells in enumerate(rows):
            for column_index, text in enumerate(cells):
                rect = pymupdf.Rect(xs[column_index] + 2, ys[row_index] + 2,
                                    xs[column_index + 1] - 2, ys[row_index + 1] - 2)
                page.insert_textbox(rect, text, fontname='korea', fontsize=6.5)
        return pdf.tobytes()


@pytest.mark.parametrize('text,expected', [('0원',0),('0만원',0),('1억0만원',100000000),('25만원',250000),('1억3천만원',130000000),
    ('1,234원',1234),('30,00만원',None),('1.5억원',None),('1억억',None),('천만원',10000000),
    ('10,000',None),('20억원',2000000000),('3천백십만원',31100000)])
def test_independent_units(text,expected):
    assert independent_won(text)==expected


@pytest.fixture
def audited(tmp_path):
    factory=create_session_factory(tmp_path/'test.sqlite3')
    create_schema(factory)
    with factory.begin() as s:
        doc=store_uploaded_document(s,BytesIO(grid_pdf()),'grid.pdf',tmp_path/'uploads',max_bytes=1000000)
        report=audit_document(s,doc)
        assert len(doc.pdf_coverages)==2
        assert report['summary_ready'],report
        assert not report['analysis_ready']
        assert all(c.confirmed_fields is None for c in doc.pdf_coverages)
        doc_id=doc.id
    return factory,doc_id,tmp_path/'uploads'


def test_real_engines_cache_provenance_and_cascade(audited):
    factory,doc_id,uploads=audited
    with factory.begin() as s:
        doc=get_document(s,doc_id)
        before=Path(doc.source_path).read_bytes()
        assert current_audit(doc)['checked']==2
        assert audit_document(s,doc)['source_counts']=={'1':2}
        assert len(doc.pdf_audits)==1
        assert all(c.review_status=='needs_review' for c in doc.pdf_coverages)
        assert Path(doc.source_path).read_bytes()==before
        delete_document_data(s,doc_id,uploads,delete_managed_original=True)
    with factory() as s:
        assert list(s.scalars(select(PdfAudit)))==[]


@pytest.mark.parametrize('damage', ['missing_row','amount','period','box','raw','source','missing_source','page'])
def test_corruption_invalidates_and_blocks(audited,damage):
    factory,doc_id,_=audited
    with factory.begin() as s:
        doc=get_document(s,doc_id)
        row=doc.pdf_coverages[0]
        if damage=='missing_row':
            doc.pdf_coverages.remove(row)
            doc.pdf_runs[0].row_count=1  # independent count must catch even synchronized metadata
        elif damage=='amount':
            row.extracted_fields=row.extracted_fields|{'amount_won':2500000}
        elif damage=='period':
            row.extracted_fields=row.extracted_fields|{'payment_period':'10년납'}
        elif damage=='box':
            row.cell_boxes=[[40,110,180,150]]*4
        elif damage=='raw':
            row.raw_cells=['검사 A','250만원','20년납90세만기','가입금액 지급']
        elif damage=='source':
            Path(doc.source_path).write_bytes(b'%PDF-changed')
        elif damage=='missing_source':
            Path(doc.source_path).unlink()
        elif damage=='page':
            row.page_number=3
        assert current_audit(doc) is None
        result=audit_document(s,doc)
        assert not result['summary_ready']
        assert not result['analysis_ready']
        assert len(doc.pdf_audits)==2


def test_user_edit_is_separate_and_requires_fresh_audit(audited):
    factory,doc_id,_=audited
    with factory.begin() as s:
        doc=get_document(s,doc_id)
        row=doc.pdf_coverages[0]
        row.confirmed_fields=row.extracted_fields|{'amount_won':240000}
        assert current_audit(doc) is None
        result=audit_document(s,doc)
        assert result['summary_ready']
        assert result['rows'][str(row.id)]['user_edited']
        assert row.extracted_fields['amount_won']==250000
        assert row.confirmed_fields['amount_won']==240000


def test_upload_auto_audit_summary_gate_rerun_and_delete(isolated_app):
    client,factory,settings=isolated_app
    response=client.post('/documents',files={'file':('grid.pdf',grid_pdf(),'application/pdf')})
    assert response.status_code==200
    with factory() as s:
        audit=s.scalar(select(PdfAudit))
        doc_id=audit.document_id
        assert audit.report['summary_ready']
    assert client.get(f'/documents/{doc_id}/summary').status_code==200
    with factory.begin() as s:
        doc=get_document(s,doc_id)
        doc.pdf_coverages[0].extracted_fields=doc.pdf_coverages[0].extracted_fields|{'amount_won':7}
    assert client.get(f'/documents/{doc_id}/summary').status_code==409
    assert client.post(f'/documents/{doc_id}/audit').status_code==200
    assert client.get(f'/documents/{doc_id}/summary').status_code==409
    assert client.post(f'/documents/{doc_id}/delete',data={'confirm_delete':'DELETE'}).status_code==200
    with factory() as s:
        assert list(s.scalars(select(PdfAudit)))==[]


def test_six_column_table_is_extracted_and_independently_audited(tmp_path):
    factory = create_session_factory(tmp_path / 'test.sqlite3')
    create_schema(factory)
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(six_column_grid_pdf()), 'six-column.pdf',
            tmp_path / 'uploads', max_bytes=1_000_000)
        report = audit_document(session, document)
        assert len(document.pdf_coverages) == 2
        assert report['summary_ready'], report
        assert report['source_counts'] == {'1': 2}
        assert document.pdf_coverages[0].extracted_fields['amount_won'] == 100_000_000
        assert document.pdf_coverages[1].extracted_fields['compensation_type'] == 'actual_cost'


def test_kyobo_image_policy_uses_ocr_line_audit_instead_of_table_boxes(tmp_path):
    factory = create_session_factory(tmp_path / 'test.sqlite3')
    create_schema(factory)
    with pymupdf.open() as pdf:
        pdf.new_page()
        source = pdf.tobytes()
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(source), 'kyobo.pdf', tmp_path / 'uploads', max_bytes=1_000_000)
        document.ocr_results.append(OcrPageResult(
            document_id=document.id, page_number=1, source_sha256=document.sha256,
            input_fingerprint='ocr', engine_version='test', languages='kor', dpi=300,
            words=[], status='ocr_ready', text=(
                '무 배 당 교 보 다 사 랑 이 보 험\n'
                '입 원 급여금 질병 또는 재해로 입원시 1일당 10,000원\n'
                '2 다 질 병 치료비 진단확정시 10,000,000원')))
        assert extract_kyobo_image_policy(document) == 2
        session.flush()

        report = audit_document(session, document)

        assert report['summary_ready'], report
        assert report['checked'] == 2
        assert report['source_counts'] == {'1': 2}
        assert all(item['passed'] for item in report['rows'].values())
        assert 'OCR 원문 행' in report['scope']


def test_reextract_replaces_only_unreviewed_machine_values(tmp_path):
    factory = create_session_factory(tmp_path / 'test.sqlite3')
    create_schema(factory)
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(six_column_grid_pdf()), 'six-column.pdf',
            tmp_path / 'uploads', max_bytes=1_000_000)
        document.condition_evidence.clear()
        document.pdf_coverages.clear()
        document.pdf_runs[0].version = 'old-reader'
        document.pdf_runs[0].warnings = []
        document.pdf_runs[0].row_count = 0
        session.flush()

        reextract_pdf_document(session, document)
        report = audit_document(session, document)

        assert len(document.pdf_coverages) == 2
        assert len(document.pdf_runs) == 1
        assert document.pdf_runs[0].row_count == 2
        assert report['summary_ready']


def test_reextract_refuses_to_overwrite_confirmed_values(tmp_path):
    factory = create_session_factory(tmp_path / 'test.sqlite3')
    create_schema(factory)
    with factory.begin() as session:
        document = store_uploaded_document(
            session, BytesIO(six_column_grid_pdf()), 'six-column.pdf',
            tmp_path / 'uploads', max_bytes=1_000_000)
        document.pdf_coverages[0].confirmed_fields = document.pdf_coverages[0].extracted_fields
        with pytest.raises(DocumentUploadError, match='사용자 확인값'):
            reextract_pdf_document(session, document)
