"""Cross-document nominal totals, produced only after every safety condition passes."""
from __future__ import annotations

from insurance_app.contract_duplicates import contract_key, duplicate_contract_candidates
from insurance_app.coverage_rules import NormalizedCoverage, can_combine, standardize_coverage_name

VERSION = "portfolio-integration-v1"

BENEFIT_TYPES = {
    "실손의료비": "medical", "일반암": "diagnosis", "유사암": "diagnosis",
    "뇌혈관질환": "diagnosis", "뇌졸중": "diagnosis", "허혈성심장질환": "diagnosis",
    "급성심근경색": "diagnosis", "질병수술": "surgery", "상해수술": "surgery",
    "입원": "admission", "후유장해": "disability", "간병": "care", "치매": "care",
    "질병사망": "death", "상해사망": "death",
}


def _normalized(row):
    if getattr(row, "extracted_fields", {}).get("source_record_type") == "contract_summary":
        return None
    fields = row.confirmed_fields or {}
    standard = standardize_coverage_name(fields.get("name", ""))
    if row.review_status != "confirmed" or standard is None:
        return None
    return NormalizedCoverage(
        original_name=fields["name"], standard_name=standard,
        compensation_type=fields.get("compensation_type", "unknown"),
        benefit_type=BENEFIT_TYPES.get(standard), period=fields.get("insurance_period", ""),
        renewal=fields.get("renewal") == "renewable",
        payment_frequency=fields.get("payment_frequency", "unknown"), confirmed=True,
    )


def portfolio_integration(subject, documents) -> dict:
    subject_documents = [doc for doc in documents if doc.subject_id == subject.id]
    duplicates = duplicate_contract_candidates(documents)
    excluded_ids = {doc_id for report in duplicates
                    if report.get("action", "do_not_combine") == "do_not_combine"
                    for doc_id in report["document_ids"]}
    grouped_ids = {doc_id for report in duplicates
                   if report.get("action") == "treat_as_one_contract"
                   for doc_id in report["document_ids"]}
    usable_documents = [doc for doc in subject_documents if doc.format == "pdf" and doc.id not in excluded_ids]
    rows_by_standard = {}
    skipped_rows = 0
    for document in usable_documents:
        for row in document.pdf_coverages:
            normalized = _normalized(row)
            if normalized is None:
                skipped_rows += 1
                continue
            rows_by_standard.setdefault(normalized.standard_name, []).append((document, row, normalized))
    groups = []
    for standard, entries in rows_by_standard.items():
        if len({contract_key(document) for document, _, _ in entries}) < 2:
            continue
        base = entries[0][2]
        comparisons = [can_combine(base, entry[2]) for entry in entries[1:]]
        if not all(allowed for allowed, _ in comparisons):
            skipped_rows += len(entries)
            continue
        groups.append({
            "standard_name": standard,
            "nominal_total_won": sum((row.confirmed_fields or {})["amount_won"] for _, row, _ in entries),
            "items": [{"document_id": document.id, "document_name": document.source_name,
                       "coverage_id": row.id, "coverage_name": normalized.original_name,
                       "page_number": row.page_number, "row_number": row.row_number}
                      for document, row, normalized in entries],
        })
    return {
        "version": VERSION, "groups": groups, "subject_document_count": len(subject_documents),
        "usable_document_count": len(usable_documents), "skipped_rows": skipped_rows,
        "duplicate_document_ids": sorted(excluded_ids & {doc.id for doc in subject_documents}),
        "grouped_document_ids": sorted(grouped_ids & {doc.id for doc in subject_documents}),
        "scope": "표시되는 금액은 같은 조건의 확정 정액형 담보를 합친 명목상 금액입니다. 실제 보험금 지급 여부나 금액을 뜻하지 않습니다.",
    }
