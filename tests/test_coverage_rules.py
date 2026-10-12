import pytest

from insurance_app.coverage_rules import NormalizedCoverage, can_combine, standardize_coverage_name


def coverage(**changes: object) -> NormalizedCoverage:
    values: dict[str, object] = {
        "original_name": "암진단담보",
        "standard_name": "일반암",
        "compensation_type": "fixed",
        "benefit_type": "diagnosis",
        "period": "80세 만기",
        "renewal": False,
        "payment_frequency": "once",
        "confirmed": True,
    }
    values.update(changes)
    return NormalizedCoverage(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("상해입원실손의료 담보", "실손의료비"),
        ("유사암 진단비", "유사암"),
        ("암진단급여금", "일반암"),
        ("뇌혈관질환 진단", "뇌혈관질환"),
        ("뇌졸중 진단", "뇌졸중"),
        ("급성심근경색증 진단", "급성심근경색"),
    ],
)
def test_standardizes_without_losing_distinct_scopes(name: str, expected: str) -> None:
    assert standardize_coverage_name(name) == expected


def test_combines_only_same_confirmed_fixed_terms() -> None:
    assert can_combine(coverage(), coverage())[0] is True


@pytest.mark.parametrize(
    "other",
    [
        coverage(confirmed=False),
        coverage(compensation_type="actual_cost"),
        coverage(standard_name="유사암"),
        coverage(standard_name="뇌졸중"),
        coverage(benefit_type="surgery"),
        coverage(period="100세 만기"),
        coverage(renewal=True),
        coverage(payment_frequency="repeat"),
        coverage(payment_frequency="unknown"),
    ],
)
def test_refuses_unsafe_combinations(other: NormalizedCoverage) -> None:
    allowed, reason = can_combine(coverage(), other)
    assert allowed is False
    assert reason
