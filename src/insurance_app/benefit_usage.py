"""Readable source-based admission, outpatient, and surgery views."""
from __future__ import annotations

from insurance_app.coverage_explanations import plain_coverage_explanation
from insurance_app.coverage_rules import standardize_coverage_name


KINDS = {
    "admission": {"title": "입원 보장", "description": "입원했을 때 증권에 적힌 금액과 지급조건을 보험별로 보여드립니다."},
    "outpatient": {"title": "통원 보장", "description": "통원 치료 때 증권에 적힌 금액과 지급조건을 보험별로 보여드립니다."},
    "surgery": {"title": "수술 보장", "description": "수술 종류별로 증권에 적힌 금액과 지급조건을 보험별로 보여드립니다."},
}


def _matches(kind: str, name: str) -> bool:
    compact = "".join((name or "").split())
    standard = standardize_coverage_name(name)
    if kind == "admission":
        return standard == "입원" or "입원" in compact
    if kind == "outpatient":
        return "통원" in compact
    return standard in {"질병수술", "상해수술"} or "수술" in compact


def benefit_usage_summary(documents, kind: str) -> dict:
    if kind not in KINDS:
        raise ValueError("지원하지 않는 보장 종류입니다.")
    groups = []
    for document in documents:
        if document.format != "pdf":
            continue
        items = []
        for row in document.pdf_coverages:
            extracted = row.extracted_fields or {}
            fields = row.confirmed_fields or extracted
            name = fields.get("name", "")
            if not _matches(kind, name):
                continue
            display_amounts = fields.get("display_amounts") or extracted.get("display_amounts") or []
            amount = fields.get("amount_won")
            amounts = [item for item in display_amounts
                       if isinstance(item, dict) and isinstance(item.get("amount_won"), int)]
            if not amounts and isinstance(amount, int):
                amounts = [{"label": name, "amount_won": amount}]
            description = fields.get("description") or extracted.get("description") or ""
            items.append({
                "coverage_id": row.id,
                "name": name,
                "amounts": amounts,
                "description": description,
                "page_number": row.page_number,
                "plain_explanation": plain_coverage_explanation(name),
                "source_status": "사용자 확인" if row.review_status == "confirmed" else "증권에서 읽은 내용",
            })
        if items:
            groups.append({"document_id": document.id, "document_name": document.source_name, "items": items})
    return {"kind": kind, **KINDS[kind], "groups": groups}
