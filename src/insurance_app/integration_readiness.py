"""Explain, without aggregating money, whether standardized rows can enter integration."""
from __future__ import annotations

from insurance_app.coverage_rules import standardize_coverage_name

VERSION = "integration-readiness-v1"


def _values(row):
    return row.confirmed_fields or row.extracted_fields


def integration_readiness(document, *, possible_duplicate: bool = False) -> dict:
    eligible = []
    blocked = []
    outside_standard = []
    for row in document.pdf_coverages:
        values = _values(row)
        name = values.get("name", "")
        standard = standardize_coverage_name(name)
        item = {
            "id": row.id,
            "name": name,
            "standard_name": standard,
            "page_number": row.page_number,
            "row_number": row.row_number,
            "reasons": [],
        }
        if standard is None:
            outside_standard.append(item)
            continue
        if row.review_status != "confirmed":
            item["reasons"].append("원문과 대조한 핵심값 확정이 필요합니다.")
        if values.get("compensation_type") == "actual_cost":
            item["reasons"].append("실손형은 가입금액을 합산하지 않습니다.")
        elif values.get("compensation_type") != "fixed":
            item["reasons"].append("정액형·실손형 구분을 확인해야 합니다.")
        for key, label in (("renewal", "갱신 여부"), ("payment_frequency", "지급 횟수"),
                           ("contract_role", "주계약·특약")):
            if values.get(key) == "unknown":
                item["reasons"].append(f"{label}를 확인해야 합니다.")
        if not values.get("insurance_period"):
            item["reasons"].append("보험기간을 확인해야 합니다.")
        if item["reasons"]:
            blocked.append(item)
        else:
            eligible.append(item)
    return {
        "version": VERSION,
        "eligible": eligible,
        "blocked": blocked,
        "outside_standard": outside_standard,
        "possible_duplicate": possible_duplicate,
        "scope": "같은 표준 담보라도 아직 금액을 합산하지 않습니다. 지급 범위·면책·감액과 다른 계약 파일의 중복 여부를 확인한 뒤에만 통합할 수 있습니다.",
    }
