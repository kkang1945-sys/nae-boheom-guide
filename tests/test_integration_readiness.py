from types import SimpleNamespace

from insurance_app.integration_readiness import integration_readiness
from test_pdf import isolated_app
from test_pdf_audit import grid_pdf


def row(name, **values):
    fields = {"name": name, "insurance_period": "90세만기", "renewal": "nonrenewable",
              "contract_role": "rider", "compensation_type": "fixed", "payment_frequency": "once"}
    fields.update(values)
    return SimpleNamespace(id=1, page_number=1, row_number=1, extracted_fields=fields,
                           confirmed_fields=fields, review_status="confirmed")


def test_only_confirmed_fixed_standard_rows_are_candidates():
    report = integration_readiness(SimpleNamespace(pdf_coverages=[row("일반암진단담보")]))
    assert report["eligible"][0]["standard_name"] == "일반암"
    assert not report["blocked"]


def test_actual_cost_and_unconfirmed_rows_are_blocked_without_money_total():
    actual = row("상해입원실손의료담보", compensation_type="actual_cost")
    unconfirmed = row("뇌졸중진단담보")
    unconfirmed.confirmed_fields = None
    unconfirmed.review_status = "needs_review"
    report = integration_readiness(SimpleNamespace(pdf_coverages=[actual, unconfirmed]))
    assert not report["eligible"]
    assert "실손형은 가입금액을 합산하지 않습니다." in report["blocked"][0]["reasons"]
    assert "원문과 대조한 핵심값 확정이 필요합니다." in report["blocked"][1]["reasons"]


def test_integration_page_is_read_only_and_requires_fresh_audit(isolated_app):
    client, _, _ = isolated_app
    response = client.post('/documents', files={'file': ('grid.pdf', grid_pdf())})
    base = response.url.path.removesuffix('/summary')
    page = client.get(base + '/integration')
    assert page.status_code == 200
    assert '현재 확인된 값만으로 통합할 수 있는 담보가 없습니다' in page.text
    assert page.headers['cache-control'] == 'no-store'
    assert client.get('/documents/99999/integration').status_code == 404
