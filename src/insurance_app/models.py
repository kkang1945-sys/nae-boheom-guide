from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from insurance_app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class InsuranceDocument(Base):
    __tablename__ = "insurance_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int | None] = mapped_column(
        ForeignKey("analysis_subjects.id", ondelete="SET NULL"), nullable=True
    )
    source_name: Mapped[str] = mapped_column(String(255))
    source_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    file_size: Mapped[int] = mapped_column(Integer)
    format: Mapped[str] = mapped_column(String(20))
    insurer_candidate: Mapped[str | None] = mapped_column(String(120))
    product_candidate: Mapped[str | None] = mapped_column(String(500))
    metadata_evidence: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    confirmed_insurer: Mapped[str | None] = mapped_column(String(120), nullable=True)
    confirmed_product: Mapped[str | None] = mapped_column(String(500), nullable=True)
    metadata_review_status: Mapped[str] = mapped_column(String(30), default="needs_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    coverages: Mapped[list[CoverageRecord]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="CoverageRecord.id"
    )
    pages: Mapped[list[DocumentPage]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="DocumentPage.page_number"
    )
    pdf_coverages: Mapped[list[PdfCoverage]] = relationship(
        cascade="all, delete-orphan", order_by="PdfCoverage.id"
    )
    pdf_runs: Mapped[list[PdfExtractionRun]] = relationship(cascade="all, delete-orphan")
    pdf_audits: Mapped[list[PdfAudit]] = relationship(cascade="all, delete-orphan", order_by="PdfAudit.id")
    condition_evidence: Mapped[list[PolicyConditionEvidence]] = relationship(
        cascade="all, delete-orphan", order_by="PolicyConditionEvidence.id"
    )
    ocr_results: Mapped[list[OcrPageResult]] = relationship(
        cascade="all, delete-orphan", order_by="OcrPageResult.id"
    )
    metadata_revisions: Mapped[list[DocumentMetadataRevision]] = relationship(
        cascade="all, delete-orphan", order_by="DocumentMetadataRevision.id"
    )
    subject: Mapped[AnalysisSubject | None] = relationship(back_populates="documents")


class AnalysisSubject(Base):
    """One person whose policies and personal funding gap will be analyzed."""

    __tablename__ = "analysis_subjects"
    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    relationship_to_user: Mapped[str] = mapped_column(String(30), default="unknown")
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    life_stage: Mapped[str] = mapped_column(String(40), default="unknown")
    economic_role: Mapped[str] = mapped_column(String(40), default="unknown")
    sex_specific_review: Mapped[str] = mapped_column(String(40), default="unknown")
    pregnancy_childbirth_review: Mapped[str] = mapped_column(String(40), default="unknown")
    review_status: Mapped[str] = mapped_column(String(30), default="needs_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    documents: Mapped[list[InsuranceDocument]] = relationship(back_populates="subject")
    financial_profile: Mapped[FinancialProfile | None] = relationship(
        back_populates="subject", cascade="all, delete-orphan", uselist=False
    )
    result_snapshots: Mapped[list[AnalysisResultSnapshot]] = relationship(
        back_populates="subject", cascade="all, delete-orphan", order_by="AnalysisResultSnapshot.id"
    )


class FinancialProfile(Base):
    """Current user-entered inputs; saved analysis snapshots are a later result feature."""
    __tablename__ = "financial_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_subjects.id", ondelete="CASCADE"), unique=True
    )
    monthly_essential_expenses_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    monthly_net_income_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    income_gap_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emergency_fund_available_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    emergency_fund_to_use_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    paid_leave_support_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loan_balance_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dependent_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    family_monthly_expenses_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    family_support_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    liquid_assets_to_use_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    guardian_monthly_income_loss_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    child_monthly_care_cost_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retirement_monthly_income_to_use_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retirement_monthly_care_cost_won: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    subject: Mapped[AnalysisSubject] = relationship(back_populates="financial_profile")


class AnalysisResultSnapshot(Base):
    """Immutable result input/output record for reopening and comparison."""
    __tablename__ = "analysis_result_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject_id: Mapped[int] = mapped_column(ForeignKey("analysis_subjects.id", ondelete="CASCADE"))
    rule_version: Mapped[str] = mapped_column(String(80))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    subject: Mapped[AnalysisSubject] = relationship(back_populates="result_snapshots")


class PdfAudit(Base):
    __tablename__ = "pdf_audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"))
    version: Mapped[str] = mapped_column(String(120))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    report: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DocumentMetadataRevision(Base):
    __tablename__ = "document_metadata_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"))
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PdfExtractionRun(Base):
    __tablename__ = "pdf_extraction_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"), unique=True)
    version: Mapped[str] = mapped_column(String(80))
    warnings: Mapped[list] = mapped_column(JSON)
    row_count: Mapped[int] = mapped_column(Integer)


class PdfCoverage(Base):
    __tablename__ = "pdf_coverages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", "row_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    row_number: Mapped[int] = mapped_column(Integer)
    extracted_fields: Mapped[dict] = mapped_column(JSON)
    raw_cells: Mapped[list] = mapped_column(JSON)
    cell_boxes: Mapped[list] = mapped_column(JSON)
    bbox: Mapped[list] = mapped_column(JSON)
    confirmed_fields: Mapped[dict | None] = mapped_column(JSON)
    review_status: Mapped[str] = mapped_column(String(30), default="needs_review")
    revisions: Mapped[list[PdfCoverageRevision]] = relationship(cascade="all, delete-orphan", order_by="PdfCoverageRevision.id")
    condition_evidence: Mapped[list[PolicyConditionEvidence]] = relationship(
        cascade="all, delete-orphan", order_by="PolicyConditionEvidence.id"
    )


class PolicyConditionEvidence(Base):
    """Source text only; this record is not a payout or coverage conclusion."""

    __tablename__ = "policy_condition_evidence"
    __table_args__ = (
        UniqueConstraint("document_id", "coverage_id", "category", "source_fingerprint"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"))
    coverage_id: Mapped[int | None] = mapped_column(
        ForeignKey("pdf_coverages.id", ondelete="CASCADE"), nullable=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(40))
    source_text: Mapped[str] = mapped_column(Text)
    source_bbox: Mapped[list | None] = mapped_column(JSON, nullable=True)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_fingerprint: Mapped[str] = mapped_column(String(64))
    extracted_detail: Mapped[dict] = mapped_column(JSON)
    review_status: Mapped[str] = mapped_column(String(30), default="machine_extracted")
    extractor_version: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revisions: Mapped[list[PolicyConditionEvidenceRevision]] = relationship(
        cascade="all, delete-orphan", order_by="PolicyConditionEvidenceRevision.id"
    )


class PolicyConditionEvidenceRevision(Base):
    __tablename__ = "policy_condition_evidence_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_id: Mapped[int] = mapped_column(ForeignKey("policy_condition_evidence.id", ondelete="CASCADE"))
    before_status: Mapped[str] = mapped_column(String(30))
    after_status: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class PdfCoverageRevision(Base):
    __tablename__ = "pdf_coverage_revisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    coverage_id: Mapped[int] = mapped_column(ForeignKey("pdf_coverages.id", ondelete="CASCADE"))
    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    width: Mapped[float] = mapped_column(Float)
    height: Mapped[float] = mapped_column(Float)
    rotation: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    words: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    reader_version: Mapped[str] = mapped_column(String(80))
    document: Mapped[InsuranceDocument] = relationship(back_populates="pages")


class OcrPageResult(Base):
    __tablename__ = "ocr_page_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    source_sha256: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(120))
    languages: Mapped[str] = mapped_column(String(30))
    dpi: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    words: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CoverageRecord(Base):
    __tablename__ = "coverage_records"
    __table_args__ = (UniqueConstraint("document_id", "table_sequence", "row_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("insurance_documents.id", ondelete="CASCADE"))
    table_sequence: Mapped[int] = mapped_column(Integer)
    row_sequence: Mapped[int] = mapped_column(Integer)
    extracted_name: Mapped[str] = mapped_column(Text)
    extracted_amount: Mapped[str] = mapped_column(String(100))
    extracted_amount_won: Mapped[int | None] = mapped_column(Integer)
    extracted_period: Mapped[str] = mapped_column(Text)
    extracted_renewal: Mapped[bool] = mapped_column(Boolean)
    compensation_type: Mapped[str] = mapped_column(String(30))
    benefit_description: Mapped[str] = mapped_column(Text)
    confirmed_name: Mapped[str | None] = mapped_column(Text)
    confirmed_amount_won: Mapped[int | None] = mapped_column(Integer)
    confirmed_period: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(30), default="needs_review")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[InsuranceDocument] = relationship(back_populates="coverages")
