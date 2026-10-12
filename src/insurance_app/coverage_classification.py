"""Read-only name grouping. No confirmed fields or monetary totals are created."""
from insurance_app.coverage_rules import STANDARD_COVERAGES, standardize_coverage_name

RULE_VERSION = "name-groups-2026-09-07-v1"


def _nonstandard_group(name: str) -> str:
    """Keep clear, out-of-scope benefits distinct from genuinely ambiguous ones."""
    compact = "".join(name.split())
    if any(keyword in compact for keyword in ("사망후유장해", "사망및상해사망", "3대암")):
        return "분류 확인 필요"
    if any(keyword in compact for keyword in ("자동차", "운전", "교통사고", "벌금", "방어비용", "면허")):
        return "자동차·법률 관련 담보 (표준군 밖)"
    if any(keyword in compact for keyword in ("골절", "화상")):
        return "상해 특정 진단·수술 (표준군 밖)"
    if "갱신형특약보험료충당" in compact:
        return "계약 유지 관련 담보 (표준군 밖)"
    if any(keyword in compact for keyword in ("강력범죄", "가족일상생활배상책임", "의료사고법률비용")):
        return "생활·법률 관련 담보 (표준군 밖)"
    return "기타 담보 (표준군 밖)"


def classification_groups(document):
    groups = {name: [] for name in (*STANDARD_COVERAGES, "분류 확인 필요")}
    for row in document.pdf_coverages:
        original = row.extracted_fields.get("name", "")
        effective = (row.confirmed_fields or {}).get("name", original)
        standard = standardize_coverage_name(effective)
        group_name = standard or _nonstandard_group(effective)
        groups.setdefault(group_name, []).append({
            "id": row.id, "original_name": original, "effective_name": effective,
            "name_changed": effective != original, "page_number": row.page_number,
            "row_number": row.row_number,
        })
    return [{"name": name, "rows": rows} for name, rows in groups.items() if rows]
