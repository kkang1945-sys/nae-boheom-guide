from io import BytesIO
from pathlib import Path

from PIL import Image
import pymupdf

from insurance_app.pdf_reader import render_page
from insurance_app.privacy_masking import (
    MASK,
    mask_document_name,
    mask_personal_information,
    mask_subject_label,
    sensitive_values,
    sensitive_word_boxes,
)


def test_text_masking_covers_identity_contact_and_labeled_identifiers():
    source = (
        "성명: 홍길동\n주민등록번호 900101-1234567\n연락처 010-1234-5678\n"
        "이메일 user@example.com\n증권번호 ABC-12345678\n주소 서울특별시 중구 세종대로 1\n"
        "가입금액 10,000,000원"
    )

    masked = mask_personal_information(source)

    assert "홍길동" not in masked
    assert "900101-1234567" not in masked
    assert "010-1234-5678" not in masked
    assert "user@example.com" not in masked
    assert "ABC-12345678" not in masked
    assert "서울특별시 중구 세종대로 1" not in masked
    assert "10,000,000원" in masked
    assert masked.count(MASK) >= 6


def test_labeled_value_is_masked_everywhere_it_repeats_in_same_text():
    masked = mask_personal_information("성명: 홍길동\n계약 관계자와 동일: 홍길동")

    assert "홍길동" not in masked
    assert masked.count(MASK) == 2


def test_known_value_from_another_page_representation_is_masked():
    masked = mask_personal_information("OCR 반복 표기 홍길동", ["홍길동"])

    assert masked == f"OCR 반복 표기 {MASK}"


def test_subject_and_document_name_remain_distinguishable_but_masked():
    assert mask_subject_label("홍길동") == "홍**"
    assert mask_document_name("홍길동보험증권.pdf") == "홍**보험증권.pdf"
    assert mask_document_name("자동차보험증권.pdf") == "자동차보험증권.pdf"


def test_word_boxes_include_direct_value_and_value_after_label():
    words = [
        [10, 10, 40, 20, "성명", 0, 0, 0],
        [45, 10, 75, 20, "홍길동", 0, 0, 1],
        [10, 30, 90, 40, "010-1234-5678", 0, 1, 0],
        [10, 50, 70, 60, "가입금액", 0, 2, 0],
        [75, 50, 140, 60, "10,000,000원", 0, 2, 1],
        [10, 70, 40, 80, "홍길동", 0, 3, 0],
    ]

    assert sensitive_word_boxes(words) == [
        [45.0, 10.0, 75.0, 20.0],
        [10.0, 30.0, 90.0, 40.0],
        [10.0, 70.0, 40.0, 80.0],
    ]


def test_word_boxes_mask_value_identified_on_another_page():
    words = [[10, 10, 40, 20, "홍길동", 0, 0, 0]]

    assert sensitive_word_boxes(words, ["홍길동"]) == [[10.0, 10.0, 40.0, 20.0]]


def test_render_page_masks_preview_without_changing_original(tmp_path: Path):
    source = tmp_path / "policy.pdf"
    document = pymupdf.open()
    page = document.new_page(width=400, height=200)
    page.insert_text((72, 72), "Name: user@example.com")
    sensitive_rect = page.search_for("user@example.com")[0]
    document.save(source)
    document.close()
    before = source.read_bytes()

    rendered = render_page(source, 1)

    assert rendered.startswith(b"\x89PNG")
    assert source.read_bytes() == before
    image = Image.open(BytesIO(rendered)).convert("RGB")
    scale = min(1.5, 1800 / 400)
    x = round((sensitive_rect.x0 + sensitive_rect.x1) / 2 * scale)
    y = round((sensitive_rect.y0 + sensitive_rect.y1) / 2 * scale)
    assert image.getpixel((x, y)) == (0, 0, 0)


def test_sensitive_values_returns_exact_source_values_for_page_redaction():
    values = sensitive_values("피보험자: 김보험 / 계약번호 ZX-998877 / 010 1234 5678")
    assert "김보험" in values
    assert "ZX-998877" in values
    assert "010 1234 5678" in values
