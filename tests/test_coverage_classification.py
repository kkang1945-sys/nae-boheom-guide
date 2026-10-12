from copy import deepcopy
from types import SimpleNamespace

import pytest

from insurance_app.coverage_classification import classification_groups
from insurance_app.coverage_rules import STANDARD_COVERAGES, standardize_coverage_name
from insurance_app.documents import get_document
from test_pdf import isolated_app
from test_pdf_audit import grid_pdf


@pytest.mark.parametrize('name', STANDARD_COVERAGES)
def test_all_standard_names(name):
    assert standardize_coverage_name(name) == name


@pytest.mark.parametrize('name', [
    '상해사망후유장해', '질병사망 및 상해사망', '뇌졸중 및 급성심근경색 진단',
    '암진단 및 유사암진단', '소액암진단', '특정암진단', '재진단암진단',
    '간병인 입원 지원', '정체불명담보',
])
def test_ambiguous_names_remain_unclassified(name):
    assert standardize_coverage_name(name) is None


def test_exclusion_whitespace_and_source_preservation():
    assert standardize_coverage_name('암 진단비(유사암 제외)') == '일반암'
    assert standardize_coverage_name('뇌혈관\n질환 진단') == '뇌혈관질환'
    row = SimpleNamespace(id=1, page_number=2, row_number=3,
        extracted_fields={'name': '원문 이름'}, confirmed_fields={'name': '뇌졸중 진단'})
    before = deepcopy(vars(row))
    groups = classification_groups(SimpleNamespace(pdf_coverages=[row]))
    assert groups[0]['name'] == '뇌졸중'
    assert groups[0]['rows'][0]['original_name'] == '원문 이름'
    assert groups[0]['rows'][0]['name_changed']
    assert vars(row) == before


@pytest.mark.parametrize(("name", "expected"), [
    ("골절수술위로금담보", "상해 특정 진단·수술 (표준군 밖)"),
    ("자동차사고부상위로금담보", "자동차·법률 관련 담보 (표준군 밖)"),
    ("가족일상생활배상책임담보", "생활·법률 관련 담보 (표준군 밖)"),
    ("일반상해사망후유장해추가담보", "분류 확인 필요"),
])
def test_nonstandard_and_ambiguous_names_are_separate(name, expected):
    row = SimpleNamespace(id=1, page_number=1, row_number=1,
        extracted_fields={"name": name}, confirmed_fields=None)
    assert classification_groups(SimpleNamespace(pdf_coverages=[row]))[0]["name"] == expected


def test_classification_page_and_stale_source_guard(isolated_app):
    client, factory, _ = isolated_app
    response = client.post('/documents', files={'file': ('grid.pdf', grid_pdf())})
    base = response.url.path.removesuffix('/summary')
    page = client.get(base + '/classification')
    assert page.status_code == 200
    assert '기타 담보 (표준군 밖) · 2개' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert '금액 합산과 보험금 지급 판단은 하지 않았습니다' in page.text
    assert base + '/pages/1?coverage_id=' in page.text
    assert base + '#pdf-coverage-' in page.text
    with factory() as session:
        doc = get_document(session, int(base.rsplit('/', 1)[1]))
        assert all(row.confirmed_fields is None for row in doc.pdf_coverages)
        from pathlib import Path
        Path(doc.source_path).write_bytes(b'%PDF-modified')
    assert client.get(base + '/classification').status_code == 409
    assert client.get('/documents/99999/classification').status_code == 404
