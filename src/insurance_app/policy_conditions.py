"""Conservative source evidence extraction; never decides whether a claim is payable."""
from __future__ import annotations

import hashlib
import re

from insurance_app.models import PolicyConditionEvidence, PolicyConditionEvidenceRevision
from insurance_app.query_layout import current_ocr_by_page, lines_from_words

VERSION = "explicit-condition-evidence-v3"

PATTERNS = {
    "exclusion": re.compile(r"지급하지|보상하지|제외|면책"),
    "reduction": re.compile(r"감액|삭감|\b(?:10|20|30|40|50|60|70|80|90)\s*[%％]"),
    "waiting_period": re.compile(
        r"보장\s*(?:책임\s*)?개시일|면책\s*기간|대기\s*기간|\b90\s*일\b"
    ),
    "payment_limit": re.compile(r"최초\s*1회|1회에\s*한|연간|매년|회당|횟수|한도"),
    "renewal_condition": re.compile(r"갱신|자동갱신"),
    "payment_trigger": re.compile(r"진단확정|수술시|입원시|사망시|발생시|지급"),
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _fingerprint(*values: object) -> str:
    return hashlib.sha256("\x1f".join(str(value) for value in values).encode("utf-8")).hexdigest()


def categories_for_text(text: str) -> list[str]:
    compact = _clean(text)
    return [category for category, pattern in PATTERNS.items() if pattern.search(compact)]


def clauses(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?:\r?\n)+|(?<=[.!?。])\s+", text or "") if part.strip()]


def _is_non_coverage_condition_line(text: str) -> bool:
    """Exclude headings and contract-administration prose from benefit conditions."""
    compact = re.sub(r"\s+", "", text or "")
    if compact == "보장내용상세(지급조건)":
        return True
    return any(marker in compact for marker in (
        "예금자보호법",
        "예금등채권의지급정지",
        "기타지급금을합한금액",
        "예상만기환급금",
        "중도인출",
        "청약의철회",
        "청약철회",
        "자동차손해배상보장법",
        "본증권은유가증권이아니므로",
        "계약자적립금",
        "책임준비금산출방법서",
        "공시이율",
        "보험기간이끝날때까지생존시",
    ))


def _condition_scope_hint(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text or "")
    if "실손의료비" in compact or "의료비본인부담유형" in compact:
        return "실손의료비 담보"
    if "간병인지원" in compact:
        return "간병인지원 관련 특약"
    if "특약" in compact and ("추가" in compact or "변경" in compact):
        return "최초가입 뒤 추가·변경한 특약"
    if "질병" in compact and "알리지" in compact and "보험금" in compact:
        return "계약 전 알릴 의무"
    if "암보장책임개시일" in compact and "90일" in compact:
        return "주계약 암 관련 담보"
    if "2년이내" in compact and ("상피내암" in compact or "진단자금" in compact):
        return "주계약 암·상피내암 진단자금 및 수술비"
    return None


def _detail(text: str, category: str) -> dict:
    return {
        "matched_explicitly": category != "benefit_text",
        "amount_mentions": re.findall(r"\d[\d,]*(?:억|천|백|십|만)?원", text),
        "frequency_mentions": re.findall(r"최초\s*1회|1회에\s*한|연간|매년|회당|횟수|한도", text),
        "scope": "coverage" if category != "document_scope_candidate" else "document_candidate",
    }


def _page_lines(page):
    if page.words and not any(len(word) >= 8 for word in page.words):
        for line in lines_from_words(page.page_number, page.words):
            yield line.text, line.bbox
        return
    grouped = {}
    for word in page.words:
        if len(word) < 8:
            continue
        grouped.setdefault((word[5], word[6]), []).append(word)
    for words in grouped.values():
        words.sort(key=lambda item: item[7])
        text = _clean(" ".join(str(item[4]) for item in words))
        bbox = [min(item[0] for item in words), min(item[1] for item in words),
                max(item[2] for item in words), max(item[3] for item in words)]
        yield text, bbox


def _evidence_pages(document):
    current = current_ocr_by_page(document)
    for page in document.pages:
        if getattr(page, "status", "text_ready") != "text_ready" and page.page_number in current:
            yield current[page.page_number]
        else:
            yield page


def _merged_condition_lines(page):
    lines = list(_page_lines(page))
    merged = []
    for source_text, bbox in lines:
        if merged and re.search(r"5\s*$", merged[-1][0]) and re.match(r"\s*0\s*[%％]", source_text):
            previous_text, previous_box = merged[-1]
            merged[-1] = (
                previous_text.rstrip() + source_text.lstrip(),
                [min(previous_box[0], bbox[0]), min(previous_box[1], bbox[1]),
                 max(previous_box[2], bbox[2]), max(previous_box[3], bbox[3])],
            )
        else:
            merged.append((source_text, bbox))
    return merged


def _inside(box, outer) -> bool:
    center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    return outer[0] <= center[0] <= outer[2] and outer[1] <= center[1] <= outer[3]


def build_condition_evidence(document) -> list[PolicyConditionEvidence]:
    """Build evidence from already source-linked PDF cells without interpreting it."""
    evidence: list[PolicyConditionEvidence] = []
    for coverage in document.pdf_coverages:
        description = _clean(coverage.extracted_fields.get("description", ""))
        if not description:
            continue
        bbox = coverage.cell_boxes[-1] if coverage.cell_boxes else coverage.bbox
        categorized = [("benefit_text", description)]
        for clause in clauses(description):
            categorized.extend((category, clause) for category in categories_for_text(clause))
        for category, source_text in categorized:
            evidence.append(PolicyConditionEvidence(
                document_id=document.id,
                coverage_id=coverage.id,
                page_number=coverage.page_number,
                category=category,
                source_text=source_text,
                source_bbox=bbox,
                source_kind="coverage_description_cell",
                source_fingerprint=_fingerprint(document.sha256, coverage.id, category, source_text, bbox),
                extracted_detail=_detail(source_text, category),
                review_status="machine_extracted",
                extractor_version=VERSION,
            ))
    coverage_boxes = {page.page_number: [row.bbox for row in document.pdf_coverages
                                         if row.page_number == page.page_number and len(row.bbox or []) == 4]
                      for page in document.pages}
    for page in _evidence_pages(document):
        for source_text, bbox in _merged_condition_lines(page):
            if len(source_text) < 8 or any(_inside(bbox, outer) for outer in coverage_boxes.get(page.page_number, [])):
                continue
            if _is_non_coverage_condition_line(source_text):
                continue
            for category in categories_for_text(source_text):
                evidence.append(PolicyConditionEvidence(
                    document_id=document.id, coverage_id=None, page_number=page.page_number,
                    category=category, source_text=source_text, source_bbox=bbox,
                    source_kind="document_page_line_candidate",
                    source_fingerprint=_fingerprint(document.sha256, page.page_number, category, source_text, bbox),
                    extracted_detail={**_detail(source_text, category), "scope": "document_candidate",
                                      "scope_hint": _condition_scope_hint(source_text)},
                    review_status="machine_extracted", extractor_version=VERSION,
                ))
    return evidence


def replace_machine_evidence(document) -> list[PolicyConditionEvidence]:
    candidates = build_condition_evidence(document)
    desired = {item.source_fingerprint: item for item in candidates}
    existing = {item.source_fingerprint: item for item in document.condition_evidence}
    for item in list(document.condition_evidence):
        if item.review_status in {"machine_extracted", "auto_checked"} and item.source_fingerprint not in desired:
            document.condition_evidence.remove(item)
    created = [item for fingerprint, item in desired.items() if fingerprint not in existing]
    document.condition_evidence.extend(created)
    return created


def auto_check_coverage_evidence(document, audit) -> int:
    """Promote only source-cell evidence after the independent transcription gate."""
    if not audit or not audit.get("summary_ready"):
        return 0
    changed = 0
    for item in document.condition_evidence:
        if item.coverage_id is not None and item.source_kind == "coverage_description_cell" \
                and item.review_status == "machine_extracted":
            row = next((row for row in document.pdf_coverages if row.id == item.coverage_id), None)
            if row and audit.get("rows", {}).get(str(row.id), {}).get("passed"):
                item.review_status = "auto_checked"
                changed += 1
    return changed


def auto_check_document_scope(document) -> int:
    """Accept scope only inside an explicit common/important-information section."""
    ranges = {}
    for page in _evidence_pages(document):
        lines = list(_page_lines(page))
        for index, (text, bbox) in enumerate(lines):
            compact_text = re.sub(r"\s+", "", text)
            if compact_text not in {"공통추가정보사항", "추가정보사항", "주요안내사항"}:
                continue
            end = float("inf")
            if compact_text == "주요안내사항":
                end = next((other_box[1] for other_text, other_box in lines[index + 1:]
                            if re.sub(r"\s+", "", other_text).startswith(("보상대상", "꼭알아두세요"))), end)
            ranges[page.page_number] = (bbox[3], end, compact_text)
            break
    changed = 0
    for item in document.condition_evidence:
        if item.coverage_id is None and item.review_status == "machine_extracted" \
                and item.page_number in ranges and item.source_bbox \
                and ranges[item.page_number][0] <= item.source_bbox[1] < ranges[item.page_number][1]:
            item.review_status = "auto_checked"
            item.extracted_detail = {**item.extracted_detail,
                "scope": "document_common",
                "scope_rule": "explicit_heading_and_bounded_position",
                "scope_heading": ranges[item.page_number][2]}
            changed += 1
    return changed


def condition_readiness(document) -> dict:
    rows = document.pdf_coverages
    evidence_by_coverage: dict[int, set[str]] = {}
    for item in document.condition_evidence:
        if item.coverage_id is not None:
            evidence_by_coverage.setdefault(item.coverage_id, set()).add(item.category)
    missing_benefit = [row.id for row in rows if "benefit_text" not in evidence_by_coverage.get(row.id, set())]
    unconfirmed = [item.id for item in document.condition_evidence
                   if item.coverage_id is not None and item.review_status not in {"confirmed", "auto_checked"}]
    unresolved_semantics = {}
    for key in ("renewal", "contract_role", "compensation_type", "payment_frequency"):
        count = sum((row.confirmed_fields or row.extracted_fields).get(key, "unknown") == "unknown" for row in rows)
        if count:
            unresolved_semantics[key] = count
    blockers = []
    if missing_benefit:
        blockers.append("원문 보장내용 근거가 없는 담보가 있습니다.")
    if unconfirmed:
        blockers.append("자동 분류한 조건 근거가 아직 사용자 확인 전입니다.")
    if unresolved_semantics:
        blockers.append("갱신·역할·보상방식·지급횟수 중 미확인 값이 있습니다.")
    document_candidates = [item.id for item in document.condition_evidence if item.coverage_id is None]
    if not document_candidates:
        blockers.append("표 밖 조건 후보를 찾지 못해 문서 범위 확인이 필요합니다.")
    elif any(item.review_status not in {"confirmed", "not_applicable", "auto_checked"}
             for item in document.condition_evidence if item.coverage_id is None):
        blockers.append("표 밖 조건 후보가 계약 전체 또는 특정 담보에 적용되는지 확인이 필요합니다.")
    document_scope_ready = bool(document_candidates) and not any(
        item.review_status not in {"confirmed", "not_applicable", "auto_checked"}
        for item in document.condition_evidence if item.coverage_id is None
    )
    return {
        "analysis_ready": not missing_benefit and not unconfirmed
        and not unresolved_semantics and document_scope_ready,
        "evidence_count": len(document.condition_evidence),
        "missing_benefit_coverage_ids": missing_benefit,
        "unconfirmed_evidence_ids": unconfirmed,
        "unresolved_semantics": unresolved_semantics,
        "document_condition_candidate_ids": document_candidates,
        "blockers": blockers,
    }


def save_document_condition_reviews(session, document, decisions: dict[int, str]) -> int:
    allowed = {"confirmed", "coverage_specific", "not_applicable"}
    targets = {item.id: item for item in document.condition_evidence if item.coverage_id is None}
    if not decisions or any(item_id not in targets or status not in allowed
                            for item_id, status in decisions.items()):
        raise ValueError("공통 조건 확인 내용을 올바르게 선택해 주세요.")
    changed = 0
    for item_id, status in decisions.items():
        item = targets[item_id]
        if item.review_status != status:
            item.revisions.append(PolicyConditionEvidenceRevision(
                before_status=item.review_status, after_status=status))
            item.review_status = status
            changed += 1
    session.flush()
    return changed
