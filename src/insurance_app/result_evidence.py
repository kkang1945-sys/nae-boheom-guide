"""Build a readable evidence trail for a current result report."""
from __future__ import annotations

from insurance_app.public_evidence import PUBLIC_EVIDENCE_SCOPE, public_program_evidence
from insurance_app.subjects import subject_context


def result_evidence(subject, report) -> dict:
    profile = subject.financial_profile
    inputs = []
    for key, label in (
        ("monthly_essential_expenses_won", "월 필수생활비"),
        ("monthly_net_income_won", "월 세후소득"),
        ("emergency_fund_available_won", "사용 가능한 비상자금"),
        ("income_gap_months", "소득중단 대비기간"),
        ("emergency_fund_to_use_won", "분석에 사용할 비상자금"),
        ("paid_leave_support_won", "유급휴가·회사 지원"),
        ("loan_balance_won", "대출잔액"),
        ("dependent_count", "부양가족 수"),
        ("family_monthly_expenses_won", "가족 월 필수생활비"),
        ("family_support_months", "사망 후 가족생활비를 준비할 기간"),
        ("liquid_assets_to_use_won", "가족 대비에 사용할 금융자산"),
        ("guardian_monthly_income_loss_won", "보호자 월 소득 감소액"),
        ("child_monthly_care_cost_won", "아동 월 추가 돌봄비"),
        ("retirement_monthly_income_to_use_won", "분석에 사용할 월 연금·지속소득"),
        ("retirement_monthly_care_cost_won", "노년기 월 의료·돌봄 본인부담"),
    ):
        value = getattr(profile, key, None) if profile else None
        inputs.append({"key": key, "label": label, "value": value})
    if subject.life_stage == "child":
        illness_formula = "아동 생활자금: max(0, (보호자 월 소득 감소액 + 아동 월 추가 돌봄비) × 대비기간 − 확정 진단비 − 사용할 비상자금 − 유급휴가·회사 지원)"
    elif subject.life_stage == "retired_older_adult":
        illness_formula = "노년기 생활자금: max(0, (월 필수생활비 + 월 의료·돌봄 본인부담) × 대비기간 − 확정 진단비 − 사용할 비상자금 − 월 연금·지속소득 × 대비기간)"
    else:
        illness_formula = "중대질환 생활자금: max(0, 월 필수생활비 × 소득중단 대비기간 − 확정 진단비 − 사용할 비상자금 − 유급휴가·회사 지원)"
    return {
        "subject_context": subject_context(subject),
        "inputs": inputs, "scenarios": report["scenarios"],
        "blocked_documents": report.get("blocked_documents", []),
        "public_programs": public_program_evidence(subject),
        "public_evidence_scope": PUBLIC_EVIDENCE_SCOPE,
        "formulae": [
            illness_formula,
            "사망 가족생활: max(0, 대출잔액 + 가족 월 필수생활비 × 사망 후 가족생활비 준비기간 − 확정 사망보험금 − 사용할 유동자산)",
        ],
        "scope": "사용자 입력, 원문 대조 확정 담보, 앱 계산식을 구분해 표시합니다. 보험금 지급 여부를 판단하지 않습니다.",
    }
