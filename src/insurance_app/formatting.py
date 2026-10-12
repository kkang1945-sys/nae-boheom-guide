import re


OCR_DISPLAY_WORDS = (
    "조혈모세포", "보장책임개시일", "주요성인병", "급성심근경색증", "장기이식수술비",
    "재해장해급여금", "재해사망보험금", "입원급여금", "골절치료비", "재해수술비",
    "질병치료비", "진단확정", "장해등급분류표", "직접적인", "직접목적으로",
    "보험기간", "가산보험금", "사망보험금", "가입금액", "안내사항", "보험금",
    "중대한", "질병", "재해로", "재해", "수술", "화상", "부식", "해당하는", "지급되었을",
    "지급되지", "않았을", "경우", "최초로", "진단받거나", "진단이", "확정되고",
    "치료를", "원인으로", "입원하여", "입원시", "통원시", "수술시", "발생시",
    "수혜자로서", "종류에서", "정하는", "한하여", "지급함", "계약", "부활",
    "일로부터", "포함하여", "지난날의", "다음날", "이후에", "이상", "초과",
    "급여금", "입원", "일당", "회당", "한도", "또는", "그리고", "으로", "인해", "목적으로",
)


def format_korean_won(value: int | None) -> str:
    """원 단위 정수를 읽기 쉬운 억/만 단위로 표시한다."""

    if value is None:
        return "금액 확인 필요"
    if value == 0:
        return "0원"
    if value < 0:
        raise ValueError("보험금액은 음수일 수 없습니다.")

    eok, remainder = divmod(value, 100_000_000)
    man, won = divmod(remainder, 10_000)
    parts: list[str] = []
    if eok:
        parts.append(f"{eok:,}억")
    if man:
        parts.append(f"{man // 1_000}천만" if man % 1_000 == 0 else f"{man:,}만")
    if won:
        parts.append(f"{won // 1_000}천" if won % 1_000 == 0 else f"{won:,}")
    return " ".join(parts) + "원"


def readable_ocr_text(value: object) -> str:
    """Hide OCR's per-syllable spacing without changing stored source text."""
    text = str(value or "")
    korean_tokens = re.findall(r"[가-힣]+", text)
    if not korean_tokens:
        return text
    single_syllables = sum(len(token) == 1 for token in korean_tokens)
    if single_syllables < 5 or single_syllables / len(korean_tokens) < 0.45:
        return text
    readable = text
    for word in OCR_DISPLAY_WORDS:
        spaced = r"\s+".join(map(re.escape, word))
        readable = re.sub(spaced, word, readable)
    return re.sub(r"[ \t]{2,}", " ", readable).strip()
