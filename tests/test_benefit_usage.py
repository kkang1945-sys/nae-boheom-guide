from types import SimpleNamespace

import pytest

from insurance_app.benefit_usage import benefit_usage_summary


def coverage(row_id, name, amount, description, *, status="needs_review", display_amounts=None):
    fields = {"name": name, "amount_won": amount, "description": description}
    if display_amounts:
        fields["display_amounts"] = display_amounts
    return SimpleNamespace(
        id=row_id, page_number=1, review_status=status,
        confirmed_fields=fields if status == "confirmed" else None,
        extracted_fields=fields,
    )


def document(rows):
    return SimpleNamespace(id=1, source_name="보험증권.pdf", format="pdf", pdf_coverages=rows)


def test_admission_view_shows_source_amount_and_condition_without_blocking():
    report = benefit_usage_summary([document([
        coverage(1, "입원급여금", 10_000, "질병 또는 재해로 입원시 1일당 10,000원"),
    ])], "admission")

    item = report["groups"][0]["items"][0]
    assert item["amounts"] == [{"label": "입원급여금", "amount_won": 10_000}]
    assert "1일당" in item["description"]
    assert item["source_status"] == "증권에서 읽은 내용"


def test_surgery_view_preserves_all_tier_amounts():
    tiers = [
        {"label": "1종 수술", "amount_won": 200_000},
        {"label": "2종 수술", "amount_won": 500_000},
        {"label": "3종 수술", "amount_won": 1_000_000},
    ]
    report = benefit_usage_summary([document([
        coverage(1, "수술비", None, "수술 종류에 따라 지급", display_amounts=tiers),
    ])], "surgery")

    assert report["groups"][0]["items"][0]["amounts"] == tiers


def test_unknown_benefit_kind_is_rejected():
    with pytest.raises(ValueError):
        benefit_usage_summary([], "unknown")
