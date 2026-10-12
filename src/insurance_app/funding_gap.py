"""Transparent calculations from user inputs and already-confirmed protection only."""
from __future__ import annotations

VERSION = "funding-gap-v2"


def family_life_required(profile) -> dict:
    """Calculate personal family funding need before subtracting any insurance."""
    missing = []
    for field, label in (
        ("loan_balance_won", "대출잔액"),
        ("dependent_count", "부양가족 수"),
        ("family_monthly_expenses_won", "가족 월 필수생활비"),
        ("family_support_months", "사망 후 가족생활비를 준비할 기간"),
        ("liquid_assets_to_use_won", "가족 대비에 사용할 금융자산"),
    ):
        if profile is None or getattr(profile, field, None) is None:
            missing.append(label)
    if missing:
        return {"status": "pending", "missing": missing, "version": VERSION,
                "scope": "보험과 무관하게 필요한 가족자금을 계산하려면 사용자 입력이 더 필요합니다."}
    required = profile.loan_balance_won + profile.family_monthly_expenses_won * profile.family_support_months
    after_assets = max(0, required - profile.liquid_assets_to_use_won)
    return {
        "status": "ready", "required_won": required,
        "liquid_assets_to_use_won": profile.liquid_assets_to_use_won,
        "need_after_assets_won": after_assets,
        "formula": "대출잔액 + (가족 월 필수생활비 × 준비기간) − 사용할 금융자산",
        "version": VERSION,
        "scope": "개인 경제상황으로 계산한 가족자금 시나리오이며 보험 가입 권장액이 아닙니다.",
    }


def death_coverage_runway(*, confirmed_death_won: int | None, loan_balance_won: int | None,
                          monthly_family_expenses_won: int | None,
                          public_monthly_reference_won: int | None = None) -> dict:
    """Show how long confirmed death cover lasts, before and after debt."""
    missing = []
    for value, label in (
        (confirmed_death_won, "확정된 사망보험금"),
        (loan_balance_won, "대출잔액"),
        (monthly_family_expenses_won, "가족 월 필수생활비"),
    ):
        if value is None:
            missing.append(label)
    if missing:
        return {"status": "pending", "missing": missing, "version": VERSION}
    if any(type(value) is not int or value < 0 for value in (
        confirmed_death_won, loan_balance_won, monthly_family_expenses_won
    )) or monthly_family_expenses_won == 0:
        return {"status": "pending", "missing": ["0원보다 큰 가족 월 필수생활비"], "version": VERSION}
    after_debt = max(0, confirmed_death_won - loan_balance_won)
    result = {
        "status": "ready",
        "confirmed_death_won": confirmed_death_won,
        "loan_balance_won": loan_balance_won,
        "after_debt_won": after_debt,
        "current_expense_months_before_debt": confirmed_death_won // monthly_family_expenses_won,
        "current_expense_months_after_debt": after_debt // monthly_family_expenses_won,
        "version": VERSION,
        "scope": "보험금이 확정 지급된다는 뜻이 아니며 물가변동·이자·세금·장례비는 반영하지 않은 단순 기간 비교입니다.",
    }
    if type(public_monthly_reference_won) is int and public_monthly_reference_won > 0:
        result["public_reference_months_after_debt"] = after_debt // public_monthly_reference_won
        result["public_monthly_reference_won"] = public_monthly_reference_won
    return result


def income_gap(profile, confirmed_diagnosis_won: int | None, life_stage: str | None = None) -> dict:
    missing = []
    for field, label in (
        ("income_gap_months", "소득중단 대비기간"),
        ("emergency_fund_to_use_won", "분석에 사용할 비상자금"),
    ):
        if profile is None or getattr(profile, field, None) is None:
            missing.append(label)
    if life_stage == "child":
        required_fields = (
            ("guardian_monthly_income_loss_won", "보호자 월 소득 감소액"),
            ("child_monthly_care_cost_won", "아동 월 추가 돌봄비"),
            ("paid_leave_support_won", "유급휴가·회사 지원"),
        )
    elif life_stage == "retired_older_adult":
        required_fields = (
            ("monthly_essential_expenses_won", "월 필수생활비"),
            ("retirement_monthly_income_to_use_won", "분석에 사용할 월 연금·지속소득"),
            ("retirement_monthly_care_cost_won", "노년기 월 의료·돌봄 본인부담"),
        )
    else:
        required_fields = (
            ("monthly_essential_expenses_won", "월 필수생활비"),
            ("paid_leave_support_won", "유급휴가·회사 지원"),
        )
    for field, label in required_fields:
        if profile is None or getattr(profile, field, None) is None:
            missing.append(label)
    if confirmed_diagnosis_won is None:
        missing.append("확정된 관련 정액 진단비")
    if missing:
        return {"status": "pending", "missing": missing,
                "scope": "핵심 입력이나 확정 담보가 없어 자금공백을 계산하지 않았습니다.", "version": VERSION}
    if life_stage == "child":
        monthly_need = profile.guardian_monthly_income_loss_won + profile.child_monthly_care_cost_won
        required = monthly_need * profile.income_gap_months
        available = confirmed_diagnosis_won + profile.emergency_fund_to_use_won + profile.paid_leave_support_won
        basis = "아동 본인의 소득을 가정하지 않고, 입력한 보호자 소득 감소와 추가 돌봄비를 사용했습니다."
        formula = "(보호자 월 소득 감소액 + 아동 월 추가 돌봄비) × 대비기간 − 확정 진단비 − 사용할 비상자금 − 유급휴가·회사 지원"
    elif life_stage == "retired_older_adult":
        monthly_need = profile.monthly_essential_expenses_won + profile.retirement_monthly_care_cost_won
        required = monthly_need * profile.income_gap_months
        recurring_income = profile.retirement_monthly_income_to_use_won * profile.income_gap_months
        available = confirmed_diagnosis_won + profile.emergency_fund_to_use_won + recurring_income
        basis = "근로소득을 가정하지 않고, 입력한 생활비·돌봄 본인부담과 사용할 연금·지속소득을 사용했습니다."
        formula = "(월 필수생활비 + 노년기 월 의료·돌봄 본인부담) × 대비기간 − 확정 진단비 − 사용할 비상자금 − (월 연금·지속소득 × 대비기간)"
    else:
        monthly_need = profile.monthly_essential_expenses_won
        required = monthly_need * profile.income_gap_months
        available = confirmed_diagnosis_won + profile.emergency_fund_to_use_won + profile.paid_leave_support_won
        basis = "사용자가 입력한 생활비와 소득중단 기간을 사용했습니다."
        formula = "월 필수생활비 × 대비기간 − 확정 진단비 − 사용할 비상자금 − 유급휴가·회사 지원"
    gap = max(0, required - available)
    threshold = monthly_need * 3
    status = "no_gap" if gap == 0 else ("large_gap" if gap >= threshold else "partial_gap")
    return {
        "status": status, "required_won": required, "available_won": available, "gap_won": gap,
        "confirmed_diagnosis_won": confirmed_diagnosis_won,
        "emergency_fund_to_use_won": profile.emergency_fund_to_use_won,
        "paid_leave_support_won": profile.paid_leave_support_won,
        "threshold_won": threshold, "version": VERSION,
        "life_stage": life_stage, "basis": basis, "formula": formula,
        "scope": basis + " 사용자 선택 시나리오이며 보험금 지급 여부를 판단하지 않습니다.",
    }


def family_life_gap(profile, confirmed_death_won: int | None) -> dict:
    missing = []
    for field, label in (
        ("loan_balance_won", "대출잔액"),
        ("dependent_count", "부양가족 수"),
        ("family_monthly_expenses_won", "가족 월 필수생활비"),
        ("family_support_months", "사망 후 가족생활비를 준비할 기간"),
        ("liquid_assets_to_use_won", "가족 대비에 사용할 금융자산"),
    ):
        if profile is None or getattr(profile, field, None) is None:
            missing.append(label)
    if confirmed_death_won is None:
        missing.append("확정된 관련 정액 사망보험금")
    if missing:
        return {"status": "pending", "missing": missing,
                "scope": "핵심 입력이나 확정 담보가 없어 가족생활 자금공백을 계산하지 않았습니다.", "version": VERSION}
    required = profile.loan_balance_won + profile.family_monthly_expenses_won * profile.family_support_months
    available = confirmed_death_won + profile.liquid_assets_to_use_won
    gap = max(0, required - available)
    threshold = profile.family_monthly_expenses_won * 3
    status = "no_gap" if gap == 0 else ("large_gap" if gap >= threshold else "partial_gap")
    return {
        "status": status, "required_won": required, "available_won": available, "gap_won": gap,
        "confirmed_death_won": confirmed_death_won, "liquid_assets_to_use_won": profile.liquid_assets_to_use_won,
        "threshold_won": threshold, "version": VERSION,
        "scope": "사용자가 입력한 가족생활비·대출·금융자산을 쓴 사망 위험 시나리오입니다. 보험금 지급 여부를 판단하지 않습니다.",
    }


def income_gap_scenarios(profile, confirmed_diagnosis_won: int | None,
                         life_stage: str | None = None) -> list[dict]:
    """Compare independent durations without changing the user's saved profile."""
    from types import SimpleNamespace
    selected = getattr(profile, 'income_gap_months', None) if profile else None
    months = sorted({3, 6, 12} | ({selected} if selected is not None else set()))
    values = {field: getattr(profile, field, None) if profile else None for field in (
        'monthly_essential_expenses_won', 'emergency_fund_to_use_won', 'paid_leave_support_won',
        'guardian_monthly_income_loss_won', 'child_monthly_care_cost_won',
        'retirement_monthly_income_to_use_won', 'retirement_monthly_care_cost_won')}
    return [income_gap(SimpleNamespace(**values, income_gap_months=duration), confirmed_diagnosis_won,
                       life_stage=life_stage)
            | {'months': duration, 'selected': duration == selected} for duration in months]
