import pytest

from insurance_app.formatting import format_korean_won, readable_ocr_text


def test_formats_won_in_readable_korean_units() -> None:
    assert format_korean_won(10_000_000) == "1천만원"
    assert format_korean_won(130_000_000) == "1억 3천만원"
    assert format_korean_won(250_000) == "25만원"
    assert format_korean_won(10_005_000) == "1천만 5천원"


def test_formats_zero_and_missing_amount() -> None:
    assert format_korean_won(0) == "0원"
    assert format_korean_won(None) == "금액 확인 필요"


def test_rejects_negative_amount() -> None:
    with pytest.raises(ValueError):
        format_korean_won(-1)


def test_readable_ocr_removes_per_syllable_spacing_only_for_ocr_like_text() -> None:
    source = "입 원 급여금 질 병 또 는 재 해 로 인 해 그 치 료 를 직 접 목 적 으 로 입 원 시"
    assert readable_ocr_text(source) == "입원 급여금 질병 또는 재해로 인해 그 치료를 직접목적으로 입원시"
    assert readable_ocr_text("질병 또는 재해로 입원 시 지급") == "질병 또는 재해로 입원 시 지급"
