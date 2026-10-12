"""Display-only masking for personal data found in local insurance documents.

The original document and extracted evidence stay unchanged so audits remain
reproducible.  These helpers are used only at presentation/export boundaries.
"""
from __future__ import annotations

import re
from pathlib import Path


MASK = "[개인정보 마스킹]"

_DIRECT_PATTERNS = (
    re.compile(r"(?<!\d)\d{6}\s*[- ]?\s*[1-8]\d{6}(?!\d)"),
    re.compile(r"(?<!\d)(?:01[016789]|0\d{1,2})[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"),
    re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE),
)

_NAME_FIELD_LABELS = r"(?:성명|계약자명|피보험자명|보험수익자명|수익자명|고객명|이름)"
_NAME_ROLE_LABELS = r"(?:계약자|피보험자|보험수익자|수익자)"
_NAME_LABELS = rf"(?:{_NAME_FIELD_LABELS}|{_NAME_ROLE_LABELS})"
_ID_LABELS = (
    r"(?:주민등록번호|외국인등록번호|고객번호|증권번호|보험증권번호|계약번호|청약번호|"
    r"계좌번호|카드번호|등록번호)"
)
_CONTACT_LABELS = r"(?:휴대전화|휴대폰|전화번호|연락처|이메일|전자우편)"
_ADDRESS_LABELS = r"(?:주소|자택주소|직장주소|소재지)"
_LABELED_PATTERNS = (
    re.compile(rf"(?P<label>{_NAME_FIELD_LABELS})(?P<sep>\s*[:：]?\s*)(?P<value>[가-힣]{{2,4}})"),
    re.compile(rf"(?P<label>{_NAME_ROLE_LABELS})(?P<sep>\s*[:：]\s*)(?P<value>[가-힣]{{2,4}})"),
    re.compile(rf"(?P<label>{_ID_LABELS}|{_CONTACT_LABELS})(?P<sep>\s*[:：]?\s*)(?P<value>[A-Z0-9@._+()/-]{{4,}})", re.IGNORECASE),
    re.compile(
        rf"(?P<label>{_ADDRESS_LABELS})(?P<sep>\s*[:：]?\s*)(?P<value>[^\r\n]{{4,}}?)(?=\r?\n|\s+(?:{_NAME_LABELS}|{_ID_LABELS}|{_CONTACT_LABELS})\b|$)"
    ),
)

_WORD_LABELS = {
    "성명": 1, "계약자": 1, "계약자명": 1, "피보험자": 1, "피보험자명": 1,
    "보험수익자": 1, "수익자": 1, "고객명": 1, "이름": 1,
    "주민등록번호": 1, "외국인등록번호": 1, "고객번호": 1, "증권번호": 1,
    "보험증권번호": 1, "계약번호": 1, "청약번호": 1, "계좌번호": 1,
    "카드번호": 1, "휴대전화": 1, "휴대폰": 1, "전화번호": 1, "연락처": 1,
    "이메일": 1, "전자우편": 1, "주소": 4, "자택주소": 4, "직장주소": 4,
    "소재지": 4,
}


def mask_personal_information(value: object, known_sensitive_values: list[str] | tuple[str, ...] = ()) -> str:
    """Mask common Korean identity/contact fields without changing stored data."""
    text = "" if value is None else str(value)
    # A value can be labelled only once and repeated elsewhere on the same page.
    # Once identified from its source label, mask every exact occurrence in this
    # presentation value.  Longer values go first so an address cannot be partly
    # exposed by replacing a shorter token contained in it.
    detected = [*sensitive_values(text), *known_sensitive_values]
    for sensitive in sorted(set(detected), key=len, reverse=True):
        if not sensitive:
            continue
        text = text.replace(sensitive, MASK)
    for pattern in _DIRECT_PATTERNS:
        text = pattern.sub(MASK, text)
    for pattern in _LABELED_PATTERNS:
        text = pattern.sub(lambda match: match.group("label") + match.group("sep") + MASK, text)
    return text


def mask_subject_label(value: object) -> str:
    """Keep a subject distinguishable while obscuring a likely real Korean name."""
    text = "" if value is None else str(value)
    safe_aliases = {"나", "본인", "배우자", "부모님", "아버지", "어머니", "자녀", "아이", "첫째 아이", "둘째 아이"}
    if text not in safe_aliases and re.fullmatch(r"[가-힣 ]{2,20}", text):
        visible = next((char for char in text if char != " "), "")
        return visible + ("*" * (sum(char != " " for char in text) - 1))
    return mask_personal_information(text)


def mask_document_name(value: object) -> str:
    """Mask a likely name prefix such as ``홍길동보험증권.pdf``."""
    text = "" if value is None else str(value)
    path = Path(text)
    stem = mask_personal_information(path.stem)
    match = re.match(r"^(?P<name>[가-힣]{2,4})(?P<kind>보험(?:증권|가입|계약|보장))", stem)
    generic_prefixes = {"자동차", "실손", "건강", "종합", "암", "어린이", "운전자", "여행자"}
    if match and match.group("name") not in generic_prefixes:
        stem = mask_subject_label(match.group("name")) + stem[match.end("name"):]
    return stem + path.suffix


def sensitive_values(text: str) -> list[str]:
    """Return exact source strings that should be redacted from a rendered page."""
    values: list[str] = []
    for pattern in _DIRECT_PATTERNS:
        values.extend(match.group(0) for match in pattern.finditer(text or ""))
    for pattern in _LABELED_PATTERNS:
        values.extend(match.group("value") for match in pattern.finditer(text or ""))
    return list(dict.fromkeys(value for value in values if value.strip()))


def sensitive_word_boxes(words: list, known_sensitive_values: list[str] | tuple[str, ...] = ()) -> list[list[float]]:
    """Find PII word boxes, including values separated from their table labels."""
    boxes: list[list[float]] = []
    repeated_sensitive_tokens: set[str] = {
        re.sub(r"[\s:：]", "", value) for value in known_sensitive_values if value
    }
    tokens = [str(word[4]).strip() if len(word) > 4 else "" for word in words]
    for index, (word, token) in enumerate(zip(words, tokens)):
        if not token or len(word) < 4:
            continue
        normalized = re.sub(r"[\s:：]", "", token)
        if any(pattern.fullmatch(token) for pattern in _DIRECT_PATTERNS):
            boxes.append([float(word[0]), float(word[1]), float(word[2]), float(word[3])])
            repeated_sensitive_tokens.add(normalized)
        count = _WORD_LABELS.get(normalized)
        if not count:
            continue
        same_line = []
        for candidate in words[index + 1:index + 1 + count + 4]:
            if len(candidate) < 5:
                continue
            if len(word) >= 8 and len(candidate) >= 8 and (candidate[5], candidate[6]) != (word[5], word[6]):
                break
            if abs(float(candidate[1]) - float(word[1])) > max(12.0, float(word[3]) - float(word[1])):
                break
            candidate_token = re.sub(r"[\s:：]", "", str(candidate[4]))
            if candidate_token in _WORD_LABELS:
                break
            if normalized in {"성명", "계약자", "계약자명", "피보험자", "피보험자명",
                              "보험수익자", "수익자", "고객명", "이름"} and not re.fullmatch(
                                  r"[가-힣]{2,4}", candidate_token):
                break
            same_line.append(candidate)
            repeated_sensitive_tokens.add(candidate_token)
            if len(same_line) >= count:
                break
        boxes.extend([[float(item[0]), float(item[1]), float(item[2]), float(item[3])] for item in same_line])
    for word, token in zip(words, tokens):
        normalized = re.sub(r"[\s:：]", "", token)
        if normalized and normalized in repeated_sensitive_tokens and len(word) >= 4:
            boxes.append([float(word[0]), float(word[1]), float(word[2]), float(word[3])])
    unique = []
    seen = set()
    for box in boxes:
        key = tuple(round(value, 2) for value in box)
        if key not in seen:
            seen.add(key)
            unique.append(box)
    return unique
