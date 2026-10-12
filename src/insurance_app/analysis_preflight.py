"""Final fail-closed gate before any funding-gap calculation."""
from __future__ import annotations

from insurance_app.ocr import current_ocr
from insurance_app.pdf_audit import current_audit
from insurance_app.policy_conditions import condition_readiness
from insurance_app.subjects import subject_readiness

VERSION = "analysis-preflight-v2"


def analysis_preflight(document) -> dict:
    checks = []

    def add(key, passed, message):
        checks.append({"key": key, "passed": bool(passed), "message": message})

    audit = current_audit(document)
    add("transcription", audit and audit.get("summary_ready"),
        "담보 표의 이름·금액·기간 자동 검산")
    subject = subject_readiness(document.subject)
    add("subject", subject["subject_ready"],
        "보험증권의 분석 대상자 지정·확인")
    add("life_stage", subject["calculation_ready"],
        "; ".join(subject["blockers"]) or "생애단계별 계산 입력 확인")
    add("metadata", document.metadata_review_status == "confirmed",
        "보험회사와 상품명 원문 확인")
    missing_ocr = [page.page_number for page in document.pages
                   if page.status != "text_ready" and not (current_ocr(document, page.page_number)
                   and current_ocr(document, page.page_number).status == "ocr_ready")]
    add("ocr", not missing_ocr,
        "이미지·스캔 페이지의 로컬 OCR 완료" if not missing_ocr
        else f"로컬 OCR 확인이 필요한 페이지: {', '.join(map(str, missing_ocr))}쪽")
    conditions = condition_readiness(document)
    add("benefit_evidence", not conditions["missing_benefit_coverage_ids"],
        "모든 담보의 보장내용 원문 근거")
    document_conditions_resolved = all(item.review_status in {"confirmed", "not_applicable", "auto_checked"}
                                       for item in document.condition_evidence if item.coverage_id is None)
    add("condition_review", not conditions["unconfirmed_evidence_ids"]
        and bool(conditions["document_condition_candidate_ids"]) and document_conditions_resolved,
        "담보별 조건과 표 밖 조건 후보의 적용 범위 확인")
    add("semantics", not conditions["unresolved_semantics"],
        "갱신·주계약/특약·정액/실손·지급횟수 확인")
    add("source", bool(document.sha256) and all(item.source_sha256 == document.sha256
        for item in document.ocr_results), "저장 원본과 OCR 근거의 해시 연결")
    return {"version": VERSION, "analysis_ready": all(item["passed"] for item in checks),
            "checks": checks, "unresolved_semantics": conditions["unresolved_semantics"],
            "scope": "분석 실행 가능 여부만 검사하며 보험금 지급 가능성을 판단하지 않습니다."}
