from types import SimpleNamespace

from insurance_app.coverage_explanations import EXPLANATIONS, explanation_groups, plain_coverage_explanation
from insurance_app.coverage_rules import STANDARD_COVERAGES
from test_pdf import isolated_app
from test_pdf_audit import grid_pdf


def test_every_standard_group_has_a_non_contract_explanation():
    assert set(EXPLANATIONS) == set(STANDARD_COVERAGES)
    groups = explanation_groups([
        {"name": "일반암", "rows": [{"id": 1}]},
        {"name": "표준군 밖", "rows": [{"id": 2}]},
    ])
    assert groups == [{"name": "일반암", "rows": [{"id": 1}], "plain_explanation": EXPLANATIONS["일반암"]}]


def test_single_coverage_explanation_is_plain_and_non_advisory():
    assert "입원" in plain_coverage_explanation("주요성인병입원비")
    assert "조건" in plain_coverage_explanation("CI보험금")
    assert "가입" not in plain_coverage_explanation("골절치료비")


def test_explanations_page_is_source_linked_and_read_only(isolated_app):
    client, _, _ = isolated_app
    response = client.post('/documents', files={'file': ('grid.pdf', grid_pdf())})
    base = response.url.path.removesuffix('/summary')
    page = client.get(base + '/explanations')
    assert page.status_code == 200
    assert '보험금 지급을 약속하지 않으며' in page.text
    assert '표준군 밖 담보' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert client.get('/documents/99999/explanations').status_code == 404
