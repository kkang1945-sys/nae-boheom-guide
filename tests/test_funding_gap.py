from types import SimpleNamespace

from insurance_app.funding_gap import (
    death_coverage_runway, family_life_gap, family_life_required, income_gap,
)


def profile(**changes):
    values = dict(monthly_essential_expenses_won=3_000_000, income_gap_months=6,
                  emergency_fund_to_use_won=3_000_000, paid_leave_support_won=0)
    values.update(changes)
    return SimpleNamespace(**values)


def test_income_gap_shows_formula_values_without_recommendation():
    result = income_gap(profile(), 10_000_000)
    assert result["required_won"] == 18_000_000
    assert result["available_won"] == 13_000_000
    assert result["gap_won"] == 5_000_000
    assert result["status"] == "partial_gap"
    assert "지급 여부" in result["scope"]


def test_large_gap_uses_documented_three_month_display_threshold():
    result = income_gap(profile(), 0)
    assert result["status"] == "large_gap"
    assert result["threshold_won"] == 9_000_000


def test_missing_inputs_or_unconfirmed_coverage_hold_calculation():
    assert income_gap(profile(income_gap_months=None), 0)["status"] == "pending"
    assert income_gap(profile(), None)["status"] == "pending"


def test_family_life_gap_separates_debt_and_family_living_costs():
    family_profile = profile(loan_balance_won=20_000_000, dependent_count=1,
                             family_monthly_expenses_won=2_000_000, family_support_months=12,
                             liquid_assets_to_use_won=4_000_000)
    result = family_life_gap(family_profile, 30_000_000)
    assert result["required_won"] == 44_000_000
    assert result["available_won"] == 34_000_000
    assert result["gap_won"] == 10_000_000
    assert result["status"] == "large_gap"


def test_duration_comparison_preserves_selected_period_and_does_not_sum():
    from types import SimpleNamespace
    from insurance_app.funding_gap import income_gap_scenarios
    profile = SimpleNamespace(monthly_essential_expenses_won=3_000_000, income_gap_months=9,
                              emergency_fund_to_use_won=3_000_000, paid_leave_support_won=0)
    rows = income_gap_scenarios(profile, 10_000_000)
    assert [row['months'] for row in rows] == [3, 6, 9, 12]
    assert [row['gap_won'] for row in rows] == [0, 5_000_000, 14_000_000, 23_000_000]
    assert profile.income_gap_months == 9
    assert sum(row['selected'] for row in rows) == 1
    assert all(row['status'] == 'pending' for row in income_gap_scenarios(None, None))


def test_user_mock_inputs_show_three_six_twelve_month_gaps_without_saving_data():
    from insurance_app.funding_gap import income_gap_scenarios
    mock = profile(monthly_essential_expenses_won=3_500_000,
                   emergency_fund_to_use_won=0, paid_leave_support_won=0)

    rows = income_gap_scenarios(mock, 20_000_000)

    assert [row['months'] for row in rows] == [3, 6, 12]
    assert [row['required_won'] for row in rows] == [10_500_000, 21_000_000, 42_000_000]
    assert [row['gap_won'] for row in rows] == [0, 1_000_000, 22_000_000]


def test_debt_alone_does_not_invent_a_family_death_scenario():
    incomplete = profile(loan_balance_won=80_000_000, dependent_count=None,
                         family_monthly_expenses_won=None, family_support_months=None,
                         liquid_assets_to_use_won=None)

    result = family_life_gap(incomplete, None)

    assert result['status'] == 'pending'
    assert {'부양가족 수', '가족 월 필수생활비', '사망 후 가족생활비를 준비할 기간',
            '가족 대비에 사용할 금융자산', '확정된 관련 정액 사망보험금'} <= set(result['missing'])


def test_death_cover_runway_shows_current_and_public_reference_after_debt():
    result = death_coverage_runway(
        confirmed_death_won=140_000_000,
        loan_balance_won=80_000_000,
        monthly_family_expenses_won=3_500_000,
        public_monthly_reference_won=1_714_892,
    )

    assert result['after_debt_won'] == 60_000_000
    assert result['current_expense_months_before_debt'] == 40
    assert result['current_expense_months_after_debt'] == 17
    assert result['public_reference_months_after_debt'] == 34


def test_death_cover_runway_never_divides_by_missing_or_zero_expenses():
    assert death_coverage_runway(confirmed_death_won=None, loan_balance_won=0,
                                 monthly_family_expenses_won=1)['status'] == 'pending'
    assert death_coverage_runway(confirmed_death_won=1, loan_balance_won=0,
                                 monthly_family_expenses_won=0)['status'] == 'pending'


def test_family_need_is_visible_before_any_death_benefit_is_confirmed():
    family_profile = profile(loan_balance_won=80_000_000, dependent_count=3,
        family_monthly_expenses_won=3_500_000, family_support_months=24,
        liquid_assets_to_use_won=0)

    result = family_life_required(family_profile)

    assert result["status"] == "ready"
    assert result["required_won"] == 164_000_000
    assert result["need_after_assets_won"] == 164_000_000
    assert "권장액이 아닙니다" in result["scope"]


def test_child_gap_uses_guardian_loss_and_care_without_assuming_child_income():
    child_profile = profile(monthly_essential_expenses_won=None,
                            guardian_monthly_income_loss_won=2_000_000,
                            child_monthly_care_cost_won=500_000)
    result = income_gap(child_profile, 5_000_000, life_stage="child")
    assert result["required_won"] == 15_000_000
    assert result["available_won"] == 8_000_000
    assert result["gap_won"] == 7_000_000
    assert "아동 본인의 소득을 가정하지 않고" in result["scope"]


def test_retirement_gap_uses_confirmed_ongoing_income_and_care_cost():
    retired_profile = profile(monthly_essential_expenses_won=2_000_000,
                              paid_leave_support_won=None,
                              retirement_monthly_income_to_use_won=1_200_000,
                              retirement_monthly_care_cost_won=300_000)
    result = income_gap(retired_profile, 2_000_000, life_stage="retired_older_adult")
    assert result["required_won"] == 13_800_000
    assert result["available_won"] == 12_200_000
    assert result["gap_won"] == 1_600_000
    assert "근로소득을 가정하지 않고" in result["scope"]
