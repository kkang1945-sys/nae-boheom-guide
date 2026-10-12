"""Anonymous end-to-end flow with two distinct policies and no personal data."""
from io import BytesIO

import pymupdf
from sqlalchemy import select

from insurance_app.documents import get_document
from insurance_app.models import AnalysisResultSnapshot, AnalysisSubject, InsuranceDocument
from test_pdf import isolated_app


def policy_pdf(product_code: str, amount_text: str) -> bytes:
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((40, 45), f"검증보험(Hi{product_code})기본형", fontname="korea", fontsize=10)
        xs = [40, 180, 270, 390, 550]
        ys = [80, 110, 155]
        for x in xs:
            page.draw_line((x, ys[0]), (x, ys[-1]))
        for y in ys:
            page.draw_line((xs[0], y), (xs[-1], y))
        rows = [
            ["담보명", "가입금액", "납기 및 만기", "보장내용"],
            ["일반암진단특약(비갱신)", amount_text, "20년납90세만기", "진단확정 시 가입금액 지급(최초 1회에 한함)"],
        ]
        for y, cells in zip([100, 137], rows):
            for x, text in zip([50, 194, 280, 400], cells):
                page.insert_text((x, y), text, fontname="korea", fontsize=8)
        page.insert_text((40, 190), "추가정보사항", fontname="korea", fontsize=9)
        page.insert_text((40, 215), "모든 담보는 최초 1회에 한함", fontname="korea", fontsize=9)
        return pdf.tobytes()


def subject_form():
    return {
        "label": "검증 대상", "relationship_to_user": "self", "birth_year": "1990",
        "life_stage": "working", "economic_role": "primary_earner",
        "sex_specific_review": "both", "pregnancy_childbirth_review": "not_applicable",
    }


def profile_form(monthly="10000000"):
    return {
        "monthly_essential_expenses_won": monthly, "monthly_net_income_won": "5000000",
        "income_gap_months": "6", "emergency_fund_available_won": "0",
        "emergency_fund_to_use_won": "0", "paid_leave_support_won": "0",
        "loan_balance_won": "0", "dependent_count": "0", "family_monthly_expenses_won": "",
        "family_support_months": "", "liquid_assets_to_use_won": "",
    }


def confirm_policy(client, factory, document_id, insurer, product):
    assert client.post(f"/documents/{document_id}/metadata", data={
        "insurer": insurer, "product": product, "source_checked": "yes"}).status_code == 200
    with factory() as session:
        document = get_document(session, document_id)
        row = document.pdf_coverages[0]
        fields = row.extracted_fields
        row_id = row.id
    response = client.post(f"/documents/{document_id}/pdf-coverages/{row_id}/review", data={
        "name": fields["name"], "amount_won": str(fields["amount_won"]),
        "insurance_period": fields["insurance_period"], "payment_period": fields["payment_period"],
        "renewal": fields["renewal"], "contract_role": fields["contract_role"],
        "compensation_type": fields["compensation_type"],
        "payment_frequency": "once", "source_checked": "yes",
    })
    assert response.status_code == 200
    assert client.get(f"/documents/{document_id}/preflight").status_code == 200
    with factory() as session:
        document = get_document(session, document_id)
        from insurance_app.analysis_preflight import analysis_preflight
        report = analysis_preflight(document)
        assert report["analysis_ready"], report


def test_two_policy_full_flow_save_reanalyze_export_and_delete(isolated_app):
    client, factory, settings = isolated_app
    for code, amount in (("1111", "1천만원"), ("2222", "2천만원")):
        uploaded = client.post("/documents", files={
            "file": (f"policy-{code}.pdf", policy_pdf(code, amount), "application/pdf")})
        assert uploaded.status_code == 200
    with factory() as session:
        document_ids = list(session.scalars(select(InsuranceDocument.id).order_by(InsuranceDocument.id)))
    assert len(document_ids) == 2
    assert client.post("/subjects", data=subject_form()).status_code == 200
    with factory() as session:
        subject_id = session.scalar(select(AnalysisSubject.id))
    for index, document_id in enumerate(document_ids, 1):
        assert client.post(f"/documents/{document_id}/subject", data={"subject_id": subject_id}).status_code == 200
        confirm_policy(client, factory, document_id, f"검증보험사{index}", f"검증상품{index}")
    assert client.post(f"/subjects/{subject_id}/financial-profile", data=profile_form()).status_code == 200

    result = client.get(f"/results?subject_id={subject_id}")
    assert result.status_code == 200
    assert "3천만원" in result.text and "소득공백 기간별 비교" in result.text
    assert client.get(f"/subjects/{subject_id}/integration").status_code == 200
    assert client.post(f"/subjects/{subject_id}/results/snapshot").status_code == 200
    with factory() as session:
        snapshot_id = session.scalar(select(AnalysisResultSnapshot.id))

    assert client.post(f"/subjects/{subject_id}/financial-profile", data=profile_form("12000000")).status_code == 200
    comparison = client.get(f"/results/snapshots/{snapshot_id}/compare")
    assert comparison.status_code == 200 and "1,200만원" in comparison.text
    assert "큰 자금공백" in comparison.text and "large_gap" not in comparison.text
    pdf = client.get(f"/results/snapshots/{snapshot_id}/export.pdf")
    csv = client.get(f"/results/snapshots/{snapshot_id}/export.csv")
    assert pdf.content.startswith(b"%PDF-")
    assert "30000000" in csv.content.decode("utf-8-sig")

    assert client.post("/data/delete", data={"confirm_delete": "DELETE_ALL"}).status_code == 200
    assert not list(settings.upload_dir.glob("*"))
    with factory() as session:
        assert session.query(InsuranceDocument).count() == 0
        assert session.query(AnalysisSubject).count() == 0
        assert session.query(AnalysisResultSnapshot).count() == 0
