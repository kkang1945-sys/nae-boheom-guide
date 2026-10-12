from io import BytesIO

import pytest
from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.documents import store_uploaded_document, delete_document_data
from insurance_app.models import PdfCoverage, PdfCoverageRevision, PdfExtractionRun
from insurance_app.pdf_coverage import fields_from_cells, fields_from_six_cells
from insurance_app.pdf_review import save_pdf_review
from test_pdf import pdf_bytes, isolated_app


def test_periods_and_unstated_renewal_stay_separate():
    fields = fields_from_cells(['확인용담보', '25만원', '20년납90세만기', '가입금액 지급'])
    assert fields['insurance_period'] == '90세만기'
    assert fields['payment_period'] == '20년납'
    assert fields['renewal'] == 'unknown'
    assert fields['contract_role'] == 'unknown'
    assert fields['payment_frequency'] == 'unknown'
    assert fields['amount_won'] == 250000


def test_renewal_and_actual_cost_not_confused_with_fixed():
    fields = fields_from_cells(['실손담보', '25만원', '전기납3년만기갱신(최대80세)', '실제부담 손해보상'])
    assert fields['insurance_period'] == '3년만기갱신(최대80세)'
    assert fields['payment_period'] == '전기납'
    assert fields['renewal'] == 'renewable'
    assert fields['compensation_type'] == 'actual_cost'
    assert fields_from_cells(['비갱신특약', '25만원', '20년납90세만기', '가입금액 지급(최초 1회)'])['renewal'] == 'nonrenewable'


def test_explicit_additional_benefit_is_a_rider():
    fields = fields_from_cells([
        '뇌졸중진단급여금추가(갱신형)담보', '2천만원',
        '전기납3년만기갱신(최대80세까지)',
        '뇌졸중 진단 확정시 가입금액 지급(최초 1회한)',
    ])
    assert fields['contract_role'] == 'rider'
    assert fields['renewal'] == 'renewable'
    assert fields['payment_frequency'] == 'once'


def test_explicit_percentage_of_policy_amount_is_fixed_but_limit_word_is_not():
    scheduled = fields_from_cells([
        '후유장해재활자금', '5천만원', '20년납100세만기',
        '가입금액의 10%를 10년간 확정지급(최초 1회한)',
    ])
    assert scheduled['compensation_type'] == 'fixed'
    assert scheduled['payment_frequency'] == 'once'

    limited = fields_from_cells([
        '치료비', '30만원', '20년납80세만기',
        '치료를 받은 경우 가입금액 한도로 보상',
    ])
    assert limited['compensation_type'] == 'unknown'

    table_payout = fields_from_cells([
        '뇌질환진단비', '3천만원', '20년납100세만기',
        '뇌질환으로 진단확정시 최초 1회한 지급',
    ])
    assert table_payout['compensation_type'] == 'fixed'

    liability = fields_from_cells([
        '일상생활중배상책임', '1억원', '20년납100세만기',
        '법률상의 배상책임 부담시 지급(자기부담금 20만원)',
    ])
    assert liability['compensation_type'] == 'unknown'


def test_one_accident_wording_is_an_explicit_per_event_frequency():
    fields = fields_from_cells([
        '골절진단비', '20만원', '20년납100세만기',
        '상해로 골절 진단확정시 1사고당 가입금액 지급',
    ])
    assert fields['compensation_type'] == 'fixed'
    assert fields['payment_frequency'] == 'per_event'


def test_unknown_amount_is_retained_not_guessed():
    fields = fields_from_cells(['확인용담보', '10,000', '', '내용 확인 필요'])
    assert fields['amount_won'] is None
    assert fields['compensation_type'] == 'unknown'
    assert fields['payment_period'] == ''


def test_six_column_fields_preserve_dates_and_apply_manwon_header_unit():
    fields = fields_from_six_cells([
        '선택계약', '갱신형 상해통원의료비\n1년납 1년만기, 갱신종료:15년',
        '2026-06-26', '2027-06-26', '25',
        '공제금액 차감후 가입금액 한도로 보상(방문 1회당)',
    ])

    assert fields['name'] == '갱신형상해통원의료비'
    assert fields['amount_text'] == '25만원'
    assert fields['amount_won'] == 250_000
    assert fields['payment_period'] == '1년납'
    assert fields['insurance_period'] == '1년만기,갱신종료:15년'
    assert fields['insurance_start'] == '2026-06-26'
    assert fields['insurance_end'] == '2027-06-26'
    assert fields['renewal'] == 'renewable'
    assert fields['contract_role'] == 'rider'
    assert fields['compensation_type'] == 'actual_cost'
    assert fields['payment_frequency'] == 'per_event'


def add_row(factory, upload_dir):
    with factory.begin() as session:
        document = store_uploaded_document(session, BytesIO(pdf_bytes()), 'mechanical.pdf', upload_dir, max_bytes=100_000)
        row = PdfCoverage(page_number=1, row_number=1, extracted_fields=fields_from_cells(
            ['확인용담보', '25만원', '20년납90세만기', '가입금액 지급']),
            raw_cells=['확인용담보', '25만원', '20년납90세만기', '가입금액 지급'],
            bbox=[50,50,200,90], cell_boxes=[[50,50,100,90]]*4,
            review_status='needs_review')
        document.pdf_coverages.append(row)
        session.flush()
        return document.id, row.id


def values(**changes):
    result = dict(name='사용자 수정값', amount_won=240000, insurance_period='90세만기',
        payment_period='20년납', renewal='unknown', contract_role='unknown',
        compensation_type='fixed', payment_frequency='unknown')
    return result | changes


def test_review_keeps_original_and_complete_revision_history(tmp_path):
    factory = create_session_factory(tmp_path/'test.sqlite3')
    create_schema(factory)
    document_id, row_id = add_row(factory, tmp_path/'uploads')
    with factory.begin() as session:
        row = save_pdf_review(session, row_id, document_id, values())
        assert row.review_status == 'needs_review'
        assert row.extracted_fields['amount_won'] == 250000
        assert row.confirmed_fields['amount_won'] == 240000
        save_pdf_review(session, row_id, document_id, values())
        assert len(row.revisions) == 1
        row = save_pdf_review(session, row_id, document_id, values(amount_won=230000,
            renewal='nonrenewable', contract_role='rider', payment_frequency='once'))
        assert row.review_status == 'confirmed'
        assert len(row.revisions) == 2
        assert row.revisions[1].before['amount_won'] == 240000
        assert row.revisions[1].after['amount_won'] == 230000
        assert row.extracted_fields['amount_won'] == 250000
    with factory.begin() as session:
        delete_document_data(session, document_id, tmp_path/'uploads', delete_managed_original=True)
    with factory() as session:
        assert not list(session.scalars(select(PdfCoverage)))
        assert not list(session.scalars(select(PdfCoverageRevision)))
        assert not list(session.scalars(select(PdfExtractionRun)))


@pytest.mark.parametrize('changes', [{'amount_won':-1}, {'renewal':'invalid'}, {'payment_period':''}])
def test_invalid_review_does_not_mutate(tmp_path, changes):
    factory = create_session_factory(tmp_path/'test.sqlite3')
    create_schema(factory)
    document_id, row_id = add_row(factory, tmp_path/'uploads')
    with pytest.raises(ValueError), factory.begin() as session:
        save_pdf_review(session, row_id, document_id, values(**changes))
    with factory() as session:
        assert session.get(PdfCoverage, row_id).confirmed_fields is None


def test_review_route_and_evidence_link(isolated_app):
    client, factory, settings = isolated_app
    document_id, row_id = add_row(factory, settings.upload_dir)
    assert '추출된 담보 후보 1개' in client.get('/').text
    url = f'/documents/{document_id}/pdf-coverages/{row_id}/review'
    assert client.post(url, data=values()).status_code == 422
    assert client.post(url, data=values() | {'source_checked':'yes'}).status_code == 200
    page = client.get(f'/documents/{document_id}/pages/1?coverage_id={row_id}')
    assert page.status_code == 200
    assert '주황 테두리' in page.text
    assert client.get(f'/documents/{document_id}/pages/2?coverage_id={row_id}').status_code == 404
    assert client.get(f'/documents/{document_id}/pages/1/image?coverage_id={row_id}').status_code == 200
    assert client.get(f'/documents/{document_id}/pages/1/image?coverage_id={row_id+1}').status_code == 404
    assert client.post(f'/documents/{document_id+1}/pdf-coverages/{row_id}/review',
        data=values() | {'source_checked':'yes'}).status_code == 404


def test_changed_source_blocks_review(isolated_app):
    from pathlib import Path
    from insurance_app.models import InsuranceDocument
    client, factory, settings = isolated_app
    document_id, row_id = add_row(factory, settings.upload_dir)
    with factory() as session:
        Path(session.get(InsuranceDocument, document_id).source_path).write_bytes(b'changed')
    url = f'/documents/{document_id}/pdf-coverages/{row_id}/review'
    assert client.post(url, data=values() | {'source_checked':'yes'}).status_code == 409
    with factory() as session:
        assert session.get(PdfCoverage, row_id).confirmed_fields is None


def test_continuation_without_outer_borders_excludes_footer(monkeypatch, tmp_path):
    """Reproduce API table fragments with anonymous cells; no policy PDF fixture."""
    from types import SimpleNamespace
    from insurance_app import pdf_coverage as reader

    class Table:
        col_count = 4
        def __init__(self, footer=False):
            self.data = [reader.HEADERS, ['확인용', '25만원', '20년납90세만기', '내용']]
            if footer:
                self.data.append(['문서 안내', '', '', ''])
            self.rows = [SimpleNamespace(cells=[(x,100+i*20,x+100,120+i*20) for x in (0,100,200,300)],
                         bbox=(0,100+i*20,400,120+i*20)) for i in range(len(self.data))]
        def extract(self):
            return self.data

    class Page:
        rect = SimpleNamespace(width=500, height=800)
        def __init__(self, number, shift=0):
            self.number, self.shift = number, shift
        def get_text(self, kind=None):
            if kind != 'dict':
                return ''
            lines = [dict(bbox=(10+i*100+self.shift,105,90+i*100+self.shift,115),
                          spans=[dict(text=header)]) for i,header in enumerate(reader.HEADERS)]
            lines.append(dict(bbox=(10,150,200,160),spans=[dict(text='추가 정보 사항')]))
            return {'blocks':[{'lines':lines}]}
        def find_tables(self, **kwargs):
            if self.number == 0:
                return SimpleNamespace(tables=[Table()])
            if 'vertical_lines' not in kwargs:
                return SimpleNamespace(tables=[])
            return SimpleNamespace(tables=[Table(footer=kwargs['clip'].y1 > 150)])

    class Document:
        def __init__(self, shift=0): self.pages=[Page(0),Page(1,shift)]
        def __enter__(self): return self.pages
        def __exit__(self,*args): pass

    source = tmp_path/'mechanical-input.bin'
    source.write_bytes(b'test')
    monkeypatch.setattr(reader.pymupdf, 'open', lambda **kwargs: Document())
    result = reader.extract_pdf_coverages(source)
    assert len(result.rows) == 2
    assert result.rows[-1]['page_number'] == 2
    assert result.rows[-1]['fields']['name'] == '확인용'
    assert not result.warnings
    # A different continuation layout must not inherit unrelated column positions.
    monkeypatch.setattr(reader.pymupdf, 'open', lambda **kwargs: Document(shift=12))
    result = reader.extract_pdf_coverages(source)
    assert len(result.rows) == 1
    assert len(result.warnings) == 1
