"""Synthetic data and a non-persistent report for the public contest demo."""
from __future__ import annotations

from hashlib import sha256

from sqlalchemy import select

from insurance_app import models
from insurance_app.coverage_rules import standardize_coverage_name


DEMO_DOCUMENTS = (
    {
        "name": "샘플 건강보험 A",
        "insurer": "샘플손해보험",
        "product": "생활안심 건강보험 (시연용)",
        "coverages": (
            ("일반암 진단비", 20_000_000, "약관에서 정한 일반암으로 진단 확정 시 최초 1회 지급"),
            ("뇌졸중 진단비", 20_000_000, "약관에서 정한 뇌졸중으로 진단 확정 시 최초 1회 지급"),
            ("급성심근경색 진단비", 20_000_000, "약관에서 정한 급성심근경색으로 진단 확정 시 최초 1회 지급"),
            ("상해사망 보험금", 130_000_000, "상해사고로 사망한 경우 가입금액 지급"),
            ("질병 입원의료비", 50_000, "질병으로 입원 치료 시 증권에 적힌 한도 내에서 보장"),
            ("질병 통원의료비", 250_000, "질병으로 통원 치료 시 증권에 적힌 한도 내에서 보장"),
        ),
    },
    {
        "name": "샘플 생활보험 B",
        "insurer": "샘플생명",
        "product": "생활보장 종합보험 (시연용)",
        "coverages": (
            ("입원 급여금", 10_000, "질병 또는 재해로 4일 이상 입원 시 3일 초과 1일당 지급"),
            ("통원 급여금", 10_000, "약관에서 정한 치료를 목적으로 통원한 경우 지급"),
            ("수술비", None, "약관의 수술 분류표에 따라 1종·2종·3종 수술비 지급"),
        ),
    },
)


def seed_public_demo(session) -> None:
    """Insert only clearly labelled synthetic policies into an empty demo database."""
    if session.scalar(select(models.InsuranceDocument.id).limit(1)) is not None:
        return
    for document_index, spec in enumerate(DEMO_DOCUMENTS, start=1):
        digest = sha256(f"public-demo-{document_index}".encode()).hexdigest()
        document = models.InsuranceDocument(
            source_name=spec["name"], source_path=f"public-demo://policy/{document_index}",
            sha256=digest, file_size=0, format="pdf",
            insurer_candidate=spec["insurer"], product_candidate=spec["product"],
            confirmed_insurer=spec["insurer"], confirmed_product=spec["product"],
            metadata_review_status="confirmed",
        )
        session.add(document)
        session.flush()
        for row_number, (name, amount, description) in enumerate(spec["coverages"], start=1):
            fields = {
                "name": name, "amount_won": amount, "description": description,
                "insurance_period": "시연용 증권 표기", "payment_period": "시연용 증권 표기",
                "renewal": "nonrenewable", "contract_role": "main",
                "compensation_type": "fixed", "payment_frequency": "once",
            }
            if name == "수술비":
                fields["display_amounts"] = [
                    {"label": "1종", "amount_won": 200_000},
                    {"label": "2종", "amount_won": 500_000},
                    {"label": "3종", "amount_won": 1_000_000},
                ]
            session.add(models.PdfCoverage(
                document_id=document.id, page_number=1, row_number=row_number,
                extracted_fields=fields, confirmed_fields=fields, review_status="confirmed",
                raw_cells=[], cell_boxes=[], bbox=[],
            ))


def public_demo_report(profile, documents) -> dict:
    """Build a concise report without pretending synthetic rows passed source-PDF audit."""
    amounts: dict[str, int] = {}
    items: dict[str, list[dict]] = {}
    for document in documents:
        for row in document.pdf_coverages:
            fields = row.confirmed_fields or row.extracted_fields or {}
            standard = standardize_coverage_name(fields.get("name", ""))
            amount = fields.get("amount_won")
            if standard and isinstance(amount, int):
                amounts[standard] = amounts.get(standard, 0) + amount
                items.setdefault(standard, []).append({
                    "document_id": document.id, "document_name": document.source_name,
                    "coverage_id": row.id, "coverage_name": fields.get("name", ""),
                    "page_number": row.page_number, "row_number": row.row_number,
                    "amount_won": amount, "payout_conditions": [],
                })
    monthly = profile.monthly_essential_expenses_won
    emergency = profile.emergency_fund_to_use_won or 0
    scenarios = []
    for standard in ("일반암", "뇌졸중", "급성심근경색"):
        amount = amounts.get(standard)
        if amount is None:
            continue
        runway_money = amount + emergency
        scenarios.append({
            "standard_name": standard, "risk_kind": "critical_illness", "status": "no_gap",
            "confirmed_diagnosis_won": amount, "reference_amount_won": amount,
            "runway_money_won": runway_money, "runway_months": runway_money // monthly,
            "items": items.get(standard, []),
        })
    death_amount = amounts.get("상해사망")
    if death_amount is not None:
        scenarios.append({
            "standard_name": "상해사망", "risk_kind": "family_life", "status": "pending",
            "reference_amount_won": death_amount, "reason": "실제 지급 여부는 사고 내용과 약관에 따라 달라집니다.",
            "items": items.get("상해사망", []),
        })
    runway_values = [item["runway_months"] for item in scenarios if "runway_months" in item]
    runway_label = (f"약 {runway_values[0]}개월" if len(set(runway_values)) == 1
                    else f"약 {min(runway_values)}~{max(runway_values)}개월")
    return {
        "version": "public-demo-v1", "transient": True,
        "subject_document_count": len(documents), "usable_document_count": len(documents),
        "documents": [{"id": item.id, "name": item.source_name} for item in documents],
        "blocked_documents": [], "scenarios": scenarios, "fallback": {},
        "result_summary": {
            "headline": f"샘플 진단비와 비상자금으로 필수생활비를 {runway_label} 감당할 수 있습니다.",
            "runway_label": runway_label, "critical_count": len(runway_values),
            "critical_scenarios": [item for item in scenarios if "runway_months" in item],
        },
        "protection_statuses": [], "coverage_checks": [], "other_coverage_groups": [],
        "family_life_need": {"status": "pending"},
        "scope": "합성 샘플 보험증권을 이용한 시연 결과이며 실제 보험금 지급 판단이 아닙니다.",
    }
