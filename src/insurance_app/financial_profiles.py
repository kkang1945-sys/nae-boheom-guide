"""Validation and readiness of user-entered economic inputs; no averages or recommendations."""
from __future__ import annotations

from types import SimpleNamespace

from insurance_app.models import FinancialProfile

AMOUNT_FIELDS = (
    "monthly_essential_expenses_won", "monthly_net_income_won", "emergency_fund_available_won",
    "emergency_fund_to_use_won", "paid_leave_support_won", "loan_balance_won",
    "family_monthly_expenses_won", "liquid_assets_to_use_won",
    "guardian_monthly_income_loss_won", "child_monthly_care_cost_won",
    "retirement_monthly_income_to_use_won", "retirement_monthly_care_cost_won",
)
COUNT_FIELDS = ("income_gap_months", "dependent_count", "family_support_months")

FIELD_LABELS = {
    "monthly_essential_expenses_won": "월 필수생활비", "monthly_net_income_won": "월 세후소득",
    "emergency_fund_available_won": "사용 가능한 비상자금", "emergency_fund_to_use_won": "분석에 사용할 비상자금",
    "paid_leave_support_won": "유급휴가·회사 지원", "loan_balance_won": "대출잔액",
    "family_monthly_expenses_won": "가족 월 필수생활비", "liquid_assets_to_use_won": "가족 대비에 사용할 금융자산",
    "guardian_monthly_income_loss_won": "보호자 월 소득 감소액",
    "child_monthly_care_cost_won": "아동 월 추가 돌봄비",
    "retirement_monthly_income_to_use_won": "분석에 사용할 월 연금·지속소득",
    "retirement_monthly_care_cost_won": "노년기 월 의료·돌봄 본인부담",
    "income_gap_months": "소득중단 대비기간", "dependent_count": "부양가족 수",
    "family_support_months": "사망 후 가족생활비를 준비할 기간",
}


def _optional_nonnegative(value, label, maximum):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}을(를) 숫자로 입력해 주세요.") from exc
    if not 0 <= number <= maximum:
        raise ValueError(f"{label}을(를) 올바른 범위로 입력해 주세요.")
    return number


def save_financial_profile(session, subject, values):
    profile = subject.financial_profile or FinancialProfile(subject=subject)
    for field in AMOUNT_FIELDS:
        setattr(profile, field, _optional_nonnegative(values.get(field), FIELD_LABELS[field], 9_000_000_000_000_000))
    for field in COUNT_FIELDS:
        maximum = 600 if field.endswith("months") else 100
        setattr(profile, field, _optional_nonnegative(values.get(field), FIELD_LABELS[field], maximum))
    if profile.emergency_fund_to_use_won is not None and profile.emergency_fund_available_won is not None \
            and profile.emergency_fund_to_use_won > profile.emergency_fund_available_won:
        raise ValueError("분석에 사용할 비상자금은 사용 가능한 비상자금을 넘을 수 없습니다.")
    if profile.id is None:
        session.add(profile)
    session.flush()
    return profile


def transient_financial_profile(values):
    """Validate a calculation-only profile without creating a database row."""
    parsed = {field: _optional_nonnegative(values.get(field), FIELD_LABELS[field],
                                           9_000_000_000_000_000)
              for field in AMOUNT_FIELDS}
    parsed.update({field: _optional_nonnegative(values.get(field), FIELD_LABELS[field],
                                                600 if field.endswith("months") else 100)
                   for field in COUNT_FIELDS})
    available = parsed["emergency_fund_available_won"]
    selected = parsed["emergency_fund_to_use_won"]
    if available is not None and selected is not None and selected > available:
        raise ValueError("분석에 사용할 비상자금은 사용 가능한 비상자금을 넘을 수 없습니다.")
    return SimpleNamespace(**parsed)


def financial_readiness(profile, life_stage: str | None = None):
    fields = {
        "income_gap_months": "소득중단 대비기간",
        "emergency_fund_to_use_won": "분석에 사용할 비상자금",
    }
    if life_stage == "child":
        fields |= {
            "guardian_monthly_income_loss_won": "보호자 월 소득 감소액",
            "child_monthly_care_cost_won": "아동 월 추가 돌봄비",
            "paid_leave_support_won": "유급휴가·회사 지원",
        }
        basis = "보호자 소득 감소와 추가 돌봄비"
    elif life_stage == "retired_older_adult":
        fields |= {
            "monthly_essential_expenses_won": "월 필수생활비",
            "retirement_monthly_income_to_use_won": "분석에 사용할 월 연금·지속소득",
            "retirement_monthly_care_cost_won": "노년기 월 의료·돌봄 본인부담",
        }
        basis = "생활비·돌봄비와 사용할 연금·지속소득"
    else:
        fields |= {
            "monthly_essential_expenses_won": "월 필수생활비",
            "paid_leave_support_won": "유급휴가·회사 지원",
        }
        basis = "월 필수생활비와 소득중단"
    missing = [label for field, label in fields.items() if profile is None or getattr(profile, field) is None]
    return {"income_gap_ready": not missing, "missing": missing, "basis": basis}
