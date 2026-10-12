from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from insurance_app.extraction import extract_policy_candidates
from insurance_app.hwpml import read_hwpml
from insurance_app.models import CoverageRecord, DocumentPage, InsuranceDocument, PdfCoverage, PdfExtractionRun, PolicyConditionEvidence, utc_now
from insurance_app.pdf_coverage import extract_pdf_coverages, VERSION
from insurance_app.pdf_reader import PdfReadError, read_pdf
from insurance_app.policy_conditions import replace_machine_evidence
from insurance_app.privacy_masking import mask_document_name
from insurance_app.query_layout import (
    VERSION as QUERY_LAYOUT_VERSION,
    extract_query_layout,
    query_layout_ready,
)
import pymupdf


class DocumentUploadError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_hwpml(session: Session, path: Path) -> InsuranceDocument:
    source = path.resolve(strict=True)
    sha256 = file_sha256(source)
    existing = session.scalar(
        select(InsuranceDocument)
        .where(InsuranceDocument.sha256 == sha256)
        .options(selectinload(InsuranceDocument.coverages))
    )
    if existing:
        return existing

    candidates = extract_policy_candidates(read_hwpml(source))
    document = InsuranceDocument(
        source_name=source.name,
        source_path=str(source),
        sha256=sha256,
        file_size=source.stat().st_size,
        format="hwpml",
        insurer_candidate=candidates.insurer,
        product_candidate=candidates.product_name,
    )
    for candidate in candidates.coverages:
        document.coverages.append(
            CoverageRecord(
                table_sequence=candidate.table_sequence,
                row_sequence=candidate.row_sequence,
                extracted_name=candidate.raw_name,
                extracted_amount=candidate.raw_amount,
                extracted_amount_won=candidate.amount_won,
                extracted_period=candidate.raw_period,
                extracted_renewal=candidate.renewal,
                compensation_type=candidate.compensation_type,
                benefit_description=candidate.benefit_description,
                review_status="needs_review",
            )
        )
    session.add(document)
    session.flush()
    return document


def _is_within(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def store_uploaded_document(
    session: Session,
    source_file: BinaryIO,
    original_name: str,
    upload_dir: Path,
    *,
    max_bytes: int,
) -> InsuranceDocument:
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".hwp", ".xml", ".pdf"}:
        raise DocumentUploadError("현재는 HWPML(.hwp)과 PDF 파일만 추가할 수 있습니다.")

    upload_dir.mkdir(parents=True, exist_ok=True)
    temporary = upload_dir / f".{uuid.uuid4().hex}.uploading"
    total = 0
    digest = hashlib.sha256()
    try:
        with temporary.open("xb") as target:
            while chunk := source_file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise DocumentUploadError("파일이 25MB보다 큽니다.")
                digest.update(chunk)
                target.write(chunk)
        if total == 0:
            raise DocumentUploadError("비어 있는 파일은 추가할 수 없습니다.")

        with temporary.open("rb") as check:
            header = check.read(16)
        if suffix == ".pdf" and not header.startswith(b"%PDF-"):
            raise DocumentUploadError("실제 PDF 파일이 아닙니다.")
        if suffix in {".hwp", ".xml"} and b"<?xml" not in header and b"<HWPML" not in header:
            raise DocumentUploadError("현재 지원하는 HWPML 형식이 아닙니다.")

        existing = session.scalar(select(InsuranceDocument).where(InsuranceDocument.sha256 == digest.hexdigest()))
        if existing:
            temporary.unlink(missing_ok=True)
            return existing

        # Validate and extract before publishing the managed original.
        pages = read_pdf(temporary) if suffix == ".pdf" else None
        pdf_candidates = extract_pdf_coverages(temporary) if pages else None
        stored = upload_dir / f"{uuid.uuid4().hex}{suffix}"
        temporary.replace(stored)
        if suffix in {".hwp", ".xml"}:
            try:
                document = register_hwpml(session, stored)
                document.source_name = mask_document_name(Path(original_name).name)
                return document
            except Exception:
                stored.unlink(missing_ok=True)
                raise

        document = InsuranceDocument(
            source_name=mask_document_name(Path(original_name).name),
            source_path=str(stored),
            sha256=digest.hexdigest(),
            file_size=total,
            format="pdf",
            insurer_candidate=None,
            product_candidate=None,
        )
        for page in pages or []:
            document.pages.append(DocumentPage(
                page_number=page.page_number, width=page.width, height=page.height,
                rotation=page.rotation, text=page.text, words=page.words,
                status=page.status, reader_version=f"pymupdf-{pymupdf.VersionBind}/evidence-v1",
            ))
        if pdf_candidates:
            document.product_candidate = pdf_candidates.product
            document.pdf_runs.append(PdfExtractionRun(version=VERSION,
                warnings=pdf_candidates.warnings, row_count=len(pdf_candidates.rows)))
            for row in pdf_candidates.rows:
                document.pdf_coverages.append(PdfCoverage(
                    page_number=row['page_number'], row_number=row['row_number'],
                    extracted_fields=row['fields'], raw_cells=row['cells'],
                    cell_boxes=row['boxes'], bbox=row['bbox'], review_status='needs_review'))
        try:
            session.add(document)
            session.flush()
            replace_machine_evidence(document)
            session.flush()
        except Exception:
            stored.unlink(missing_ok=True)
            raise
        return document
    except PdfReadError as exc:
        temporary.unlink(missing_ok=True)
        raise DocumentUploadError(str(exc)) from exc
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def reextract_pdf_document(session: Session, document: InsuranceDocument) -> InsuranceDocument:
    """Re-run extraction without overwriting any user-reviewed source evidence."""
    if document.format != "pdf":
        raise DocumentUploadError("PDF 문서만 다시 추출할 수 있습니다.")
    source = Path(document.source_path)
    if not source.is_file() or file_sha256(source) != document.sha256:
        raise DocumentUploadError("보관 원본이 없거나 변경되어 다시 추출할 수 없습니다.")
    if any(row.confirmed_fields is not None or row.revisions for row in document.pdf_coverages):
        raise DocumentUploadError("사용자 확인값이나 수정 이력이 있어 자동 재추출로 덮을 수 없습니다.")
    if any(item.revisions or item.review_status not in {"machine_extracted", "auto_checked"}
           for item in document.condition_evidence):
        raise DocumentUploadError("사용자가 확인한 조건 근거가 있어 자동 재추출로 덮을 수 없습니다.")

    candidates = extract_pdf_coverages(source)
    extraction_version = VERSION
    if not candidates.rows and query_layout_ready(document):
        query_result = extract_query_layout(document)
        candidates = query_result
        extraction_version = QUERY_LAYOUT_VERSION
    document.condition_evidence.clear()
    document.pdf_coverages.clear()
    session.flush()
    if not candidates.rows:
        # Image-only legacy policies can have no geometric PDF table at all.
        # Reuse their already stored local OCR instead of leaving a zero-row run.
        from insurance_app.ocr_policy_coverage import extract_kyobo_image_policy
        if extract_kyobo_image_policy(document):
            session.flush()
            replace_machine_evidence(document)
            session.flush()
            return document
    run = document.pdf_runs[0] if document.pdf_runs else PdfExtractionRun()
    run.version = extraction_version
    run.warnings = candidates.warnings
    run.row_count = len(candidates.rows)
    if not document.pdf_runs:
        document.pdf_runs.append(run)
    document.product_candidate = (
        getattr(candidates, "product", None)
        or getattr(candidates, "metadata", {}).get("product")
        or document.product_candidate
    )
    for row in candidates.rows:
        document.pdf_coverages.append(PdfCoverage(
            page_number=row['page_number'], row_number=row['row_number'],
            extracted_fields=row['fields'], raw_cells=row['cells'],
            cell_boxes=row['boxes'], bbox=row['bbox'], review_status='needs_review'))
    session.flush()
    replace_machine_evidence(document)
    session.flush()
    return document


def delete_document_data(
    session: Session,
    document_id: int,
    upload_dir: Path,
    *,
    delete_managed_original: bool,
) -> bool:
    document = session.get(InsuranceDocument, document_id)
    if document is None:
        return False
    source = Path(document.source_path)
    if delete_managed_original and _is_within(source, upload_dir):
        from insurance_app.file_deletion import stage_file_deletion
        stage_file_deletion(session, source, upload_dir)
    session.delete(document)
    session.flush()
    return True


def list_documents(session: Session) -> list[InsuranceDocument]:
    return list(
        session.scalars(
            select(InsuranceDocument)
            .options(selectinload(InsuranceDocument.coverages), selectinload(InsuranceDocument.pdf_coverages),
                     selectinload(InsuranceDocument.subject))
            .order_by(InsuranceDocument.created_at.desc())
        )
    )


def get_document(session: Session, document_id: int) -> InsuranceDocument | None:
    return session.scalar(
        select(InsuranceDocument)
        .where(InsuranceDocument.id == document_id)
        .options(selectinload(InsuranceDocument.coverages), selectinload(InsuranceDocument.pages),
                 selectinload(InsuranceDocument.pdf_coverages).selectinload(PdfCoverage.revisions),
                 selectinload(InsuranceDocument.pdf_coverages).selectinload(PdfCoverage.condition_evidence),
                 selectinload(InsuranceDocument.pdf_runs), selectinload(InsuranceDocument.pdf_audits),
                 selectinload(InsuranceDocument.condition_evidence).selectinload(PolicyConditionEvidence.revisions),
                 selectinload(InsuranceDocument.subject),
                 selectinload(InsuranceDocument.ocr_results), selectinload(InsuranceDocument.metadata_revisions))
    )


def confirm_coverage(
    session: Session,
    coverage_id: int,
    *,
    name: str,
    amount_won: int,
    period: str,
) -> CoverageRecord | None:
    coverage = session.get(CoverageRecord, coverage_id)
    if coverage is None:
        return None
    if not name.strip() or not period.strip() or amount_won < 0:
        raise ValueError("담보명, 금액, 기간을 올바르게 입력해 주세요.")
    coverage.confirmed_name = name.strip()
    coverage.confirmed_amount_won = amount_won
    coverage.confirmed_period = period.strip()
    coverage.review_status = "confirmed"
    coverage.confirmed_at = utc_now()
    session.flush()
    return coverage
