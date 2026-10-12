"""Conservative duplicate-contract candidates; never merges documents automatically."""
from __future__ import annotations

import re


def _compact(value):
    return re.sub(r"\s+", "", value or "").lower()


def coverage_signatures(document):
    if document.format == "pdf":
        return {(_compact(row.extracted_fields.get("name")), row.extracted_fields.get("amount_won"),
                 _compact(row.extracted_fields.get("raw_period"))) for row in document.pdf_coverages}
    return {(_compact(row.extracted_name), row.extracted_amount_won, _compact(row.extracted_period))
            for row in document.coverages}


def contract_group_fingerprint(document):
    evidence = getattr(document, "metadata_evidence", None) or {}
    query = evidence.get("query_layout") or {}
    return query.get("contract_group_fingerprint")


def contract_key(document):
    """Return a logical contract key without exposing its printed identifier."""
    return contract_group_fingerprint(document) or f"document:{document.id}"


def same_contract_groups(documents):
    """Return verified multi-document query groups without exposing their fingerprints."""
    grouped = {}
    for document in documents:
        fingerprint = contract_group_fingerprint(document)
        if fingerprint:
            grouped.setdefault(fingerprint, []).append(document)
    return [sorted(members, key=lambda item: item.id) for members in grouped.values() if len(members) > 1]


def duplicate_contract_candidates(documents, *, minimum_rows=5, minimum_overlap=0.8):
    reports = []
    for index, left in enumerate(documents):
        left_rows = coverage_signatures(left)
        for right in documents[index + 1:]:
            left_group = contract_group_fingerprint(left)
            right_group = contract_group_fingerprint(right)
            if left_group and left_group == right_group:
                reports.append({
                    "document_ids": (left.id, right.id),
                    "shared_rows": len(left_rows & coverage_signatures(right)),
                    "overlap": None,
                    "product_match": True,
                    "status": "same_contract_group",
                    "action": "treat_as_one_contract",
                    "match_basis": "masked_contract_identifier_and_product",
                })
                continue
            right_rows = coverage_signatures(right)
            denominator = min(len(left_rows), len(right_rows))
            if denominator < minimum_rows:
                continue
            shared = len(left_rows & right_rows)
            overlap = shared / denominator
            product_match = bool(left.product_candidate and right.product_candidate and
                                 _compact(left.product_candidate) == _compact(right.product_candidate))
            if overlap >= minimum_overlap:
                reports.append({"document_ids": (left.id, right.id), "shared_rows": shared,
                                "overlap": overlap, "product_match": product_match,
                                "status": "possible_duplicate", "action": "do_not_combine"})
    return reports
