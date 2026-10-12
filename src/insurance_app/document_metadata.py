"""Source-linked document metadata candidates and separate user confirmation."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy.orm import Session

from insurance_app.documents import file_sha256
from insurance_app.models import DocumentMetadataRevision
from insurance_app.query_layout import query_metadata


_INSURER_SUFFIX = r"(?:해상화재보험|화재해상보험|손해보험|생명보험|보험주식회사)"
_LABELED_INSURER_SUFFIX = rf"(?:{_INSURER_SUFFIX}|화재|생명)"


def _labeled_insurer_from_text(text: str) -> str | None:
    """Prefer the company explicitly identified as the policy issuer."""
    for line in (text or "").splitlines():
        compact = re.sub(r"\s+", "", line)
        match = re.search(
            rf"(?:발행자|보험회사|보험사|회사명)[:：]?"
            rf"(?P<name>[가-힣A-Za-z&·㈜()]{{2,40}}{_LABELED_INSURER_SUFFIX})",
            compact,
        )
        if match:
            return match.group("name")
    return None


def _insurer_from_ocr(text: str) -> str | None:
    """Read only an explicit company-name line, tolerating OCR spaces."""
    labeled = _labeled_insurer_from_text(text)
    if labeled:
        return labeled
    for line in (text or "").splitlines():
        compact = re.sub(r"\s+", "", line)
        compact = re.sub(r"^(?:보험회사|보험사|회사명)[:：]?", "", compact)
        if len(compact) > 60:
            continue
        matches = re.findall(rf"[가-힣A-Za-z&·㈜]{{2,40}}{_INSURER_SUFFIX}", compact)
        if matches:
            return matches[0]
    return None


def _product_from_text(text: str) -> str | None:
    """Read a compact product-title line without treating policy prose as a title."""
    lines = [re.sub(r"\s+", "", line) for line in (text or "").splitlines()]
    for compact in lines:
        match = re.fullmatch(r"(?:상품명|보험상품명)[:：](?P<name>.{3,100})", compact)
        if match and "보험" in match.group("name"):
            return match.group("name")
    for compact in lines:
        if (4 <= len(compact) <= 80 and compact.startswith("무배당") and "보험" in compact
                and not any(mark in compact for mark in ("입니다", "합니다", "됩니다"))):
            return compact
        if re.fullmatch(r".+보험\(Hi\d+\).+", compact):
            return compact
    return None


def detect_metadata_candidates(document) -> dict:
    query = query_metadata(document)
    insurer = None
    insurer_page = None
    insurer_source = "unresolved"
    for page in document.pages:
        insurer = _labeled_insurer_from_text(page.text)
        if insurer:
            insurer_page = page.page_number
            insurer_source = "direct_pdf_text_label"
            break
    if not insurer:
        for result in reversed(document.ocr_results):
            insurer = _insurer_from_ocr(result.text)
            if insurer:
                insurer_page = result.page_number
                insurer_source = "local_ocr"
                break
    if not insurer and query.get("insurer"):
        insurer = query["insurer"]
        insurer_page = 1
        insurer_source = "direct_web_inquiry_header"

    product = None
    product_page = None
    product_source = "unresolved"
    for page in document.pages:
        product = _product_from_text(page.text)
        if product:
            product_page = page.page_number
            product_source = "direct_pdf_text"
            break
    if not product:
        for result in reversed(document.ocr_results):
            product = _product_from_text(result.text)
            if product:
                product_page = result.page_number
                product_source = "local_ocr"
                break
    if not product and query.get("product"):
        product = query["product"]
        product_page = query.get("product_page")
        product_source = "local_ocr_query_layout"
    if not product:
        product = document.product_candidate
        if product:
            compact_product = re.sub(r"\s+", "", product)
            product_page = next((page.page_number for page in document.pages
                                 if compact_product in re.sub(r"\s+", "", page.text)), None)
            product_source = "existing_candidate"

    if insurer:
        document.insurer_candidate = insurer
    if product:
        document.product_candidate = product
    document.metadata_evidence = {
        "source_sha256": document.sha256,
        "insurer": {"value": document.insurer_candidate, "page_number": insurer_page,
                    "source": insurer_source},
        "product": {"value": product, "page_number": product_page,
                    "source": product_source},
        "query_layout": query or None,
    }
    return document.metadata_evidence


def confirm_document_metadata(session: Session, document, *, insurer: str, product: str):
    source = Path(document.source_path)
    if not source.is_file() or file_sha256(source) != document.sha256:
        raise ValueError("원본 파일이 없거나 변경되어 메타정보를 확인할 수 없습니다.")
    insurer, product = insurer.strip(), product.strip()
    if not insurer or not product:
        raise ValueError("보험회사와 상품명을 모두 확인해 주세요.")
    before = ({"insurer": document.confirmed_insurer, "product": document.confirmed_product}
              if document.confirmed_insurer or document.confirmed_product else None)
    after = {"insurer": insurer, "product": product}
    if before != after:
        document.metadata_revisions.append(DocumentMetadataRevision(before=before, after=after))
    document.confirmed_insurer = insurer
    document.confirmed_product = product
    document.metadata_review_status = "confirmed"
    session.flush()
    return document
