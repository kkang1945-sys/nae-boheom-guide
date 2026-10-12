from insurance_app.extraction import extract_policy_candidates, parse_korean_won
from insurance_app.hwpml import HwpmlDocument, TableRow


def test_parses_korean_money_units() -> None:
    assert parse_korean_won("1억원") == 100_000_000
    assert parse_korean_won("1억3천만원") == 130_000_000
    assert parse_korean_won("2천만원") == 20_000_000
    assert parse_korean_won("25만원") == 250_000
    assert parse_korean_won("단위없음") is None


def test_extracts_four_cell_coverage_as_unconfirmed_candidate() -> None:
    document = HwpmlDocument(
        source_name="sample.hwp",
        blocks=(),
        table_rows=(
            TableRow(1, 1, ("암진단담보", "2천만원", "3년만기갱신", "진단 확정 시 지급")),
        ),
    )

    result = extract_policy_candidates(document)

    assert len(result.coverages) == 1
    candidate = result.coverages[0]
    assert candidate.amount_won == 20_000_000
    assert candidate.renewal is True
    assert candidate.review_status == "needs_review"


def test_marks_actual_cost_coverage_without_combining_it() -> None:
    document = HwpmlDocument(
        source_name="sample.hwp",
        blocks=(),
        table_rows=(
            TableRow(2, 3, ("질병입원실손의료", "5천만원", "3년만기갱신", "실제 부담 비용 한도")),
        ),
    )

    result = extract_policy_candidates(document)

    assert result.coverages[0].compensation_type == "actual_cost"
