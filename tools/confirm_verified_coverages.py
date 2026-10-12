"""Record an explicit user review for source-verified, fully resolved PDF rows.

This maintenance command never invents unresolved semantic fields. It copies the
immutable extraction into the user-confirmed layer only when every analysis
semantic is already resolved, and writes the normal revision/audit trail.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from insurance_app.config import load_settings
from insurance_app.database import create_session_factory
from insurance_app.document_metadata import confirm_document_metadata
from insurance_app.documents import get_document, reextract_pdf_document
from insurance_app.pdf_audit import audit_document
from insurance_app.pdf_review import CHOICES, save_pdf_review
from insurance_app.policy_conditions import auto_check_coverage_evidence, auto_check_document_scope


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def confirm_verified_rows(document, session, *, resolve_explicit_additional_riders: bool = False) -> tuple[list[int], list[int]]:
    confirmed: list[int] = []
    unresolved: list[int] = []
    for row in document.pdf_coverages:
        values = dict(row.extracted_fields)
        name = values.get("name", "")
        if (resolve_explicit_additional_riders and values.get("contract_role") == "unknown"
                and "추가" in name and name.endswith("담보")):
            values["contract_role"] = "rider"
        if any(values.get(key, "unknown") == "unknown" for key in CHOICES):
            unresolved.append(row.id)
            continue
        save_pdf_review(session, row.id, document.id, values)
        confirmed.append(row.id)
    return confirmed, unresolved


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--document-id", type=int, required=True)
    parser.add_argument("--insurer", required=True)
    parser.add_argument("--product", required=True)
    parser.add_argument("--refresh-extraction", action="store_true")
    parser.add_argument("--resolve-explicit-additional-riders", action="store_true")
    parser.add_argument("--database-path", type=Path)
    args = parser.parse_args()

    settings = load_settings()
    factory = create_session_factory(args.database_path or settings.database_path)
    with factory.begin() as session:
        document = get_document(session, args.document_id)
        if document is None or document.format != "pdf":
            raise SystemExit("PDF document not found")
        source = Path(document.source_path)
        if not source.is_file() or _sha256(source) != document.sha256:
            raise SystemExit("stored source is missing or its hash changed")
        if args.refresh_extraction:
            reextract_pdf_document(session, document)
            initial_report = audit_document(session, document)
            auto_check_coverage_evidence(document, initial_report)
            auto_check_document_scope(document)
        confirm_document_metadata(session, document, insurer=args.insurer, product=args.product)
        confirmed, unresolved = confirm_verified_rows(
            document, session,
            resolve_explicit_additional_riders=args.resolve_explicit_additional_riders,
        )
        refreshed = get_document(session, document.id)
        report = audit_document(session, refreshed)
        auto_check_coverage_evidence(refreshed, report)
        print(f"document={document.id} confirmed={len(confirmed)} unresolved={len(unresolved)}")
        print("confirmed_ids=" + ",".join(map(str, confirmed)))
        print("unresolved_ids=" + ",".join(map(str, unresolved)))


if __name__ == "__main__":
    main()
