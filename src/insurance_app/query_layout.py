"""Conservative reader for image-based insurer web inquiry printouts.

The reader consumes source-bound local OCR.  It recognizes only the Kyobo
inquiry layout identified by its embedded URL and never promotes contract
summary amounts to confirmed payout benefits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import re


VERSION = "kyobo-web-query-v1"
LAYOUT = "kyobo_web_query_v1"

QUERY_PATHS = {
    "inquiry-special-contract": "riders",
    "inquiry-contract-detail": "contract",
    "inquiry-guarantee-history": "benefits",
}

RIDER_NAMES = (
    "주계약",
    "4대암치료특약",
    "항암치료특약",
    "양성종양수술특약",
)

BENEFIT_NAMES = (
    "암진단자금",
    "암수술비",
    "암입원비",
    "암간병자금",
    "상피내암진단자금",
    "상피내암수술비",
    "상피내암입원비",
    "상피내암간병자금",
)

NON_RISK_BENEFIT_NAMES = ("만기급여금",)


@dataclass(frozen=True)
class OcrLine:
    page_number: int
    text: str
    compact: str
    bbox: list[float]
    words: list


@dataclass
class QueryLayoutResult:
    kind: str | None = None
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def compact(value: object) -> str:
    return re.sub(r"\s+", "", "" if value is None else str(value))


def query_kind(document) -> str | None:
    direct_text = "\n".join(page.text or "" for page in document.pages)
    if "kyobo.com" not in direct_text.lower():
        return None
    for path, kind in QUERY_PATHS.items():
        if path in direct_text:
            return kind
    return None


def current_ocr_by_page(document) -> dict[int, object]:
    current = {}
    for result in getattr(document, "ocr_results", []):
        if result.source_sha256 == document.sha256 and result.status == "ocr_ready":
            current[result.page_number] = result
    return current


def query_layout_ready(document) -> bool:
    if not query_kind(document):
        return False
    current = current_ocr_by_page(document)
    return bool(document.pages) and all(page.page_number in current for page in document.pages)


def _bbox(words: list) -> list[float]:
    return [
        min(float(word[0]) for word in words),
        min(float(word[1]) for word in words),
        max(float(word[2]) for word in words),
        max(float(word[3]) for word in words),
    ]


def lines_from_words(page_number: int, words: list) -> list[OcrLine]:
    """Rebuild visual lines when stored OCR words no longer carry TSV line ids."""
    usable = [word for word in words if len(word) >= 5 and str(word[4]).strip()
              and len(str(word[4])) <= 200
              and not any(character in str(word[4]) for character in ("\t", "\r", "\n"))]
    usable.sort(key=lambda word: ((float(word[1]) + float(word[3])) / 2, float(word[0])))
    groups: list[list] = []
    centers: list[float] = []
    for word in usable:
        center = (float(word[1]) + float(word[3])) / 2
        height = max(1.0, float(word[3]) - float(word[1]))
        target = None
        best = None
        for index, group_center in enumerate(centers):
            distance = abs(center - group_center)
            if distance <= max(4.5, height * 0.75) and (best is None or distance < best):
                target, best = index, distance
        if target is None:
            groups.append([word])
            centers.append(center)
        else:
            groups[target].append(word)
            centers[target] = sum((float(item[1]) + float(item[3])) / 2 for item in groups[target]) / len(groups[target])
    result = []
    for words_in_line in groups:
        words_in_line.sort(key=lambda word: float(word[0]))
        text = " ".join(str(word[4]).strip() for word in words_in_line)
        result.append(OcrLine(page_number, text, compact(text), _bbox(words_in_line), words_in_line))
    return sorted(result, key=lambda line: (line.page_number, line.bbox[1], line.bbox[0]))


def document_lines(document) -> list[OcrLine]:
    current = current_ocr_by_page(document)
    return [line for page in document.pages for line in lines_from_words(
        page.page_number, current[page.page_number].words if page.page_number in current else []
    )]


def _find_product(lines: list[OcrLine]) -> tuple[str | None, int | None]:
    for line in lines:
        match = re.search(r"(무배당[가-힣A-Za-z0-9·]+보험(?:\([^)]{1,20}\))?)", line.compact)
        if match:
            return match.group(1), line.page_number
    return None, None


def _find_masked_identifier(lines: list[OcrLine]) -> tuple[str | None, int | None]:
    for line in lines:
        if "증권번호" not in line.compact and "보험증권번호" not in line.compact:
            continue
        match = re.search(r"(?<!\d)(\d{5}\*{4}\d{3})(?!\d)", line.compact)
        if match:
            return match.group(1), line.page_number
    return None, None


def _contract_details(lines: list[OcrLine]) -> dict:
    text = "\n".join(line.compact for line in lines)

    def value(label: str, pattern: str):
        match = re.search(re.escape(label) + pattern, text)
        return match.group(1) if match else None

    return {
        "contract_date": value("계약일", r"(\d{4}[.]\d{2}[.]\d{2})"),
        "insurance_period": value("보험기간", r"(\d+년)"),
        "payment_period": value("납입기간", r"(\d+년)"),
        "maturity_date": value("보험만기일", r"(\d{4}[.]\d{2}[.]\d{2})"),
        "contract_status": "유지" if "계약상태유지" in text else None,
    }


def query_metadata(document, lines: list[OcrLine] | None = None) -> dict:
    kind = query_kind(document)
    if not kind:
        return {}
    lines = lines if lines is not None else document_lines(document)
    product, product_page = _find_product(lines)
    identifier, identifier_page = _find_masked_identifier(lines)
    fingerprint = None
    if product and identifier:
        payload = f"{VERSION}|교보생명|{compact(product).lower()}|{identifier}"
        fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "layout": LAYOUT,
        "kind": kind,
        "insurer": "교보생명",
        "product": product,
        "product_page": product_page,
        "contract_group_fingerprint": fingerprint,
        "contract_identifier_page": identifier_page,
        "contract_identifier_storage": "sha256_only",
        "contract_details": _contract_details(lines) if kind == "contract" else {},
    }


def _section_lines(all_lines: list[OcrLine], starts: list[tuple[int, OcrLine]]) -> list[tuple[str, OcrLine, list[OcrLine]]]:
    ordered = sorted(starts, key=lambda item: (item[1].page_number, item[1].bbox[1]))
    positions = {(line.page_number, line.bbox[1]): index for index, line in enumerate(all_lines)}
    sections = []
    for index, (name, start) in enumerate(ordered):
        start_index = positions[(start.page_number, start.bbox[1])]
        end_index = len(all_lines)
        if index + 1 < len(ordered):
            following = ordered[index + 1][1]
            end_index = positions[(following.page_number, following.bbox[1])]
        sections.append((name, start, all_lines[start_index + 1:end_index]))
    return sections


def _section_bbox(start: OcrLine, lines: list[OcrLine]) -> list[float]:
    same_page = [line for line in lines if line.page_number == start.page_number]
    boxes = [start.bbox, *(line.bbox for line in same_page)]
    return [min(box[0] for box in boxes), min(box[1] for box in boxes),
            max(box[2] for box in boxes), max(box[3] for box in boxes)]


def _line_after_label(lines: list[OcrLine], label: str) -> OcrLine | None:
    return next((line for line in lines if label in line.compact), None)


def _amount_line(line: OcrLine) -> bool:
    """Match the fixed left label while tolerating observed Korean OCR glyph swaps."""
    value = line.compact
    return bool(re.match(r"^(?:가입금[액맥]|가[인이]그[애액])", value))


def _money_from_text(text: str, *, default_ten_thousand: bool) -> int | None:
    value = compact(text).replace(",/", ",").replace("/ ,", ",").replace("/，", ",")
    formatted = list(re.finditer(
        r"(?<![\d,./])(\d{1,4}(?:[,./]\d{3})+)(?P<unit>만원|만[원위워]|마[원위워]|원)?(?![\d,./])",
        value,
    ))
    if formatted:
        match = formatted[-1] if _amount_line(OcrLine(0, value, value, [0, 0, 0, 0], [])) else formatted[0]
        amount = int(re.sub(r"[,./]", "", match.group(1)))
        unit = match.group("unit") or ""
        if unit.startswith(("만", "마")) or (default_ten_thousand and not unit):
            amount *= 10_000
        return amount
    if default_ten_thousand:
        plain = re.search(r"(?<!\d)(\d{1,6})(?:만원|만[원위워]|마[원위워])(?!\d)", value)
        if plain:
            return int(plain.group(1)) * 10_000
    return None


def _amount_from_words(
    words: list,
    page_width: float,
    *,
    left_ratio: float = 0.55,
    default_ten_thousand: bool = False,
) -> tuple[int | None, str, list[float] | None]:
    """Read one money value without concatenating frequency/limit digits into it."""
    right = [word for word in words
             if (float(word[0]) + float(word[2])) / 2 >= page_width * left_ratio]
    for line in lines_from_words(0, right):
        text = " ".join(str(word[4]) for word in sorted(line.words, key=lambda item: float(item[0])))
        amount = _money_from_text(text, default_ten_thousand=default_ten_thousand)
        if amount is None:
            continue
        numeric_words = [word for word in line.words if re.search(r"\d", str(word[4]))]
        if numeric_words:
            max_digits = max(len(re.sub(r"\D", "", str(word[4]))) for word in numeric_words)
            numeric_words = [word for word in numeric_words
                             if len(re.sub(r"\D", "", str(word[4]))) == max_digits]
        return amount, f"{amount:,}원", _bbox(numeric_words) if numeric_words else line.bbox
    return None, "", None


def _frequency(text: str) -> str:
    value = compact(text)
    if "최초1회" in value or "1회한" in value:
        return "once"
    if "수술1회당" in value or "1회당" in value:
        return "per_event"
    if "1일당" in value or "일초과1일당" in value:
        return "per_day"
    return "unknown"


def _benefit_rows(document, lines: list[OcrLine]) -> tuple[list, list[str]]:
    starts = []
    boundaries = []
    for line in lines:
        matches = [name for name in BENEFIT_NAMES + NON_RISK_BENEFIT_NAMES
                   if name in line.compact and len(line.compact) <= len(name) + 8]
        if matches:
            name = max(matches, key=len)
            boundaries.append((name, line))
            if name in BENEFIT_NAMES:
                starts.append((name, line))
    rows = []
    warnings = []
    page_widths = {page.page_number: page.width for page in document.pages}
    risk_starts = {(line.page_number, line.bbox[1]): name for name, line in starts}
    for name, start, section in _section_lines(lines, boundaries):
        if (start.page_number, start.bbox[1]) not in risk_starts:
            continue
        same_page = [line for line in section if line.page_number == start.page_number]
        section_words = [word for line in same_page for word in line.words]
        amount, amount_text, amount_box = _amount_from_words(section_words, page_widths[start.page_number])
        if amount is None:
            warnings.append(f"확인 필요: {name}의 보장금액을 같은 원문 항목에서 연결하지 못했습니다.")
            continue
        content_lines = [line for line in same_page if not any(marker in line.compact for marker in (
            "보장내용", "보장금액", "kyobo.com", "/4"
        ))]
        description = "\n".join(line.text for line in content_lines).strip()
        description_box = _bbox([word for line in content_lines for word in line.words]) if content_lines else start.bbox
        raw_period = ""
        fields = {
            "name": name,
            "amount_text": amount_text,
            "amount_won": amount,
            "raw_period": raw_period,
            "insurance_period": "",
            "payment_period": "",
            "renewal": "unknown",
            "contract_role": "main",
            "compensation_type": "fixed",
            "payment_frequency": _frequency(description),
            "description": description,
            "source_layout": LAYOUT,
            "source_record_type": "benefit_detail",
            "source_pages": [start.page_number],
        }
        rows.append({
            "page_number": start.page_number,
            "row_number": len(rows) + 1,
            "fields": fields,
            "cells": [name, amount_text, raw_period, description],
            "boxes": [start.bbox, amount_box, None, description_box],
            "bbox": _section_bbox(start, section),
        })
    found_non_risk = [name for name in NON_RISK_BENEFIT_NAMES if any(line.compact == name for line in lines)]
    if found_non_risk:
        warnings.append("정보: 만기급여금은 위험보장 담보와 구분해 담보 후보에서 제외했습니다.")
    return rows, warnings


def _period_value(line: OcrLine | None, label: str) -> str:
    if not line:
        return ""
    match = re.search(re.escape(label) + r"(\d+년)", line.compact)
    return match.group(1) if match else ""


def _rider_rows(document, lines: list[OcrLine]) -> tuple[list, list[str]]:
    starts = []
    for line in lines:
        matches = [name for name in RIDER_NAMES
                   if name in line.compact and len(line.compact) <= len(name) + 8]
        if matches:
            starts.append((max(matches, key=len), line))
    rows = []
    warnings = []
    page_widths = {page.page_number: page.width for page in document.pages}
    for name, start, section in _section_lines(lines, starts):
        amount_line = next((line for line in section if _amount_line(line)), None)
        if not amount_line:
            warnings.append(f"확인 필요: {name}의 가입금액 원문 행을 찾지 못했습니다.")
            continue
        amount, amount_text, amount_box = _amount_from_words(
            amount_line.words, page_widths[amount_line.page_number],
            left_ratio=0.35, default_ten_thousand=True,
        )
        insurance = _period_value(_line_after_label(section, "보험기간"), "보험기간")
        payment = _period_value(_line_after_label(section, "납입기간"), "납입기간")
        if amount is None or not insurance or not payment:
            warnings.append(f"확인 필요: {name}의 가입금액·보험기간·납입기간을 같은 원문 항목에서 연결하지 못했습니다.")
            continue
        raw_period = payment + insurance
        status_line = _line_after_label(section, "특약상태")
        description = "계약 항목 요약" + (" · 특약상태 유지" if status_line and "유지" in status_line.compact else "")
        fields = {
            "name": name,
            "amount_text": amount_text,
            "amount_won": amount,
            "raw_period": raw_period,
            "insurance_period": insurance,
            "payment_period": payment,
            "renewal": "unknown",
            "contract_role": "main" if name == "주계약" else "rider",
            "compensation_type": "unknown",
            "payment_frequency": "unknown",
            "description": description,
            "source_layout": LAYOUT,
            "source_record_type": "contract_summary",
            "source_pages": sorted({start.page_number, *(line.page_number for line in section)}),
        }
        boxes = [start.bbox, amount_box, _bbox([word for line in section if "기간" in line.compact for word in line.words]), start.bbox]
        rows.append({
            "page_number": start.page_number,
            "row_number": len(rows) + 1,
            "fields": fields,
            "cells": [name, amount_text, raw_period, description],
            "boxes": boxes,
            "bbox": _section_bbox(start, section),
        })
    return rows, warnings


def extract_query_layout(document) -> QueryLayoutResult:
    kind = query_kind(document)
    result = QueryLayoutResult(kind=kind)
    if not kind:
        return result
    if not query_layout_ready(document):
        result.warnings.append("조회형 문서의 모든 페이지에 로컬 OCR이 필요합니다.")
        return result
    lines = document_lines(document)
    result.metadata = query_metadata(document, lines)
    if kind == "benefits":
        result.rows, result.warnings = _benefit_rows(document, lines)
    elif kind == "riders":
        result.rows, result.warnings = _rider_rows(document, lines)
    return result
