"""Persist reproducible result reports without altering source evidence."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from insurance_app.models import AnalysisResultSnapshot
from insurance_app.privacy_masking import mask_subject_label
from insurance_app.result_evidence import result_evidence

VERSION = "result-snapshot-v2"


def save_result_snapshot(session, subject, report) -> AnalysisResultSnapshot:
    report = deepcopy(report)
    report["subject_label"] = mask_subject_label(subject.label)
    report["evidence"] = result_evidence(subject, report)
    report["snapshot_version"] = VERSION
    report["subject_inputs"] = {key: getattr(subject, key) for key in (
        "relationship_to_user", "birth_year", "life_stage", "economic_role",
        "sex_specific_review", "pregnancy_childbirth_review", "review_status")}
    report["contract_inputs"] = [{
        "document_id": document.id, "sha256": document.sha256,
        "confirmed_insurer": document.confirmed_insurer, "confirmed_product": document.confirmed_product,
        "metadata_review_status": document.metadata_review_status,
        "coverages": [{"coverage_id": row.id, "confirmed_fields": deepcopy(row.confirmed_fields),
                       "review_status": row.review_status} for row in document.pdf_coverages],
        "condition_evidence": [{"id": item.id, "fingerprint": item.source_fingerprint,
                                "review_status": item.review_status} for item in document.condition_evidence],
    } for document in subject.documents]
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    record = AnalysisResultSnapshot(
        subject=subject, rule_version=report["version"],
        input_fingerprint=hashlib.sha256(payload.encode("utf-8")).hexdigest(), report=report,
    )
    session.add(record)
    session.flush()
    return record
