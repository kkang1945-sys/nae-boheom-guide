"""Analysis-subject rules. Demographics select scenarios; they never set recommended coverage amounts."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from insurance_app.contract_duplicates import contract_group_fingerprint
from insurance_app.financial_profiles import financial_readiness
from insurance_app.models import AnalysisSubject, InsuranceDocument

RELATIONSHIPS = {"self", "child", "spouse", "parent", "other"}
LIFE_STAGES = {
    "child", "pre_work", "young_adult", "working", "middle_age",
    "retirement_transition", "retired_older_adult",
}
ECONOMIC_ROLES = {"primary_earner", "secondary_earner", "unpaid_caregiver", "dependent", "retired", "mixed"}
SEX_SPECIFIC_REVIEWS = {"female_related", "male_related", "both", "not_applicable"}
PREGNANCY_REVIEWS = {"include", "not_applicable"}

LIFE_STAGE_LABELS = {
    "child": "아동·청소년", "pre_work": "청년·경제활동 전",
    "young_adult": "청년·경제활동 중", "working": "경제활동 중",
    "middle_age": "중년", "retirement_transition": "은퇴 전환기",
    "retired_older_adult": "노년·은퇴기",
}

LIFE_STAGE_FOCUS = {
    "child": (
        "아이 본인의 소득대체액을 만들지 않고 보호자의 소득 감소와 추가 돌봄비를 확인합니다.",
        "영유아·아동 대상 공적 의료·돌봄 제도의 실제 적용 여부를 확인합니다.",
    ),
    "pre_work": (
        "생활비를 실제로 부담하는 사람과 사용할 가족 자금의 범위를 확인합니다.",
        "본인 소득이 없다는 이유만으로 필요한 보장을 자동 계산하지 않습니다.",
    ),
    "young_adult": (
        "현재 생활비·소득중단 기간·비상자금·부채를 본인 입력으로 확인합니다.",
        "결혼이나 부양가족이 있을 때만 가족생활 공백을 계산합니다.",
    ),
    "working": ("현재 생활비·소득중단 기간·비상자금·유급휴가와 실제 부양책임을 확인합니다.",),
    "middle_age": (
        "본인 소득공백과 자녀·부모 부양책임, 대출, 돌봄 역할을 각각 확인합니다.",
        "부양책임을 나이만으로 가정하지 않습니다.",
    ),
    "retirement_transition": (
        "근로소득이 끝나는 시점과 실제 수령할 연금·퇴직소득을 구분해 확인합니다.",
        "현재 부채와 향후 돌봄비를 별도 항목으로 검토합니다.",
    ),
    "retired_older_adult": (
        "실제 수령 중인 연금·지속소득과 의료·돌봄 본인부담을 확인합니다.",
        "노인장기요양보험 등은 인정 결과와 실제 본인부담이 확인된 경우에만 금액 계산에 반영합니다.",
    ),
}


def subject_context(subject: AnalysisSubject) -> dict:
    current_year = datetime.now(timezone.utc).year
    age = current_year - subject.birth_year if subject.birth_year is not None else None
    sex_focus = {
        "female_related": "여성 관련 담보를 원문에서 확인",
        "male_related": "남성 관련 담보를 원문에서 확인",
        "both": "성별 관련 담보를 원문 그대로 모두 확인",
        "not_applicable": "성별 관련 담보 별도 검토 없음",
    }.get(subject.sex_specific_review, "성별 관련 담보 범위 확인 필요")
    return {
        "stage_label": LIFE_STAGE_LABELS.get(subject.life_stage, subject.life_stage),
        "approximate_age": age,
        "age_note": "출생연도 기준 연도 차이이며 생일을 반영한 만 나이가 아닙니다." if age is not None else None,
        "focus": list(LIFE_STAGE_FOCUS.get(subject.life_stage, ())),
        "sex_focus": sex_focus,
        "pregnancy_focus": subject.pregnancy_childbirth_review == "include",
    }


def save_subject(session: Session, *, label: str, relationship_to_user: str,
                 birth_year: int | None, life_stage: str, economic_role: str,
                 sex_specific_review: str, pregnancy_childbirth_review: str,
                 subject: AnalysisSubject | None = None) -> AnalysisSubject:
    label = label.strip()
    current_year = datetime.now(timezone.utc).year
    if not label:
        raise ValueError("분석 대상자를 구분할 이름을 입력해 주세요.")
    if relationship_to_user not in RELATIONSHIPS:
        raise ValueError("대상자와의 관계를 확인해 주세요.")
    if birth_year is not None and not 1900 <= birth_year <= current_year:
        raise ValueError("출생연도를 확인해 주세요.")
    if life_stage not in LIFE_STAGES or economic_role not in ECONOMIC_ROLES:
        raise ValueError("생애단계와 경제적 역할을 확인해 주세요.")
    if sex_specific_review not in SEX_SPECIFIC_REVIEWS:
        raise ValueError("성별 관련 담보 검토 범위를 확인해 주세요.")
    if pregnancy_childbirth_review not in PREGNANCY_REVIEWS:
        raise ValueError("임신·출산 관련 담보 검토 여부를 확인해 주세요.")
    target = subject or AnalysisSubject()
    target.label = label
    target.relationship_to_user = relationship_to_user
    target.birth_year = birth_year
    target.life_stage = life_stage
    target.economic_role = economic_role
    target.sex_specific_review = sex_specific_review
    target.pregnancy_childbirth_review = pregnancy_childbirth_review
    target.review_status = "confirmed"
    if subject is None:
        session.add(target)
    session.flush()
    return target


def _contract_group_members(session: Session, document: InsuranceDocument) -> list[InsuranceDocument]:
    fingerprint = contract_group_fingerprint(document)
    if not fingerprint:
        return []
    return [
        candidate for candidate in session.scalars(select(InsuranceDocument).order_by(InsuranceDocument.id))
        if contract_group_fingerprint(candidate) == fingerprint
    ]


def assign_document(session: Session, document_id: int, subject_id: int) -> InsuranceDocument:
    document = session.get(InsuranceDocument, document_id)
    subject = session.get(AnalysisSubject, subject_id)
    if document is None or subject is None:
        raise LookupError("보험증권 또는 분석 대상자를 찾을 수 없습니다.")
    if len(_contract_group_members(session, document)) > 1:
        raise ValueError("같은 계약 조회자료는 묶음 전체를 한 대상자에게 연결해 주세요.")
    document.subject = subject
    session.flush()
    return document


def assign_contract_group(session: Session, document_id: int, subject_id: int) -> list[InsuranceDocument]:
    """Assign every document in one verified query-document group to one subject."""
    document = session.get(InsuranceDocument, document_id)
    subject = session.get(AnalysisSubject, subject_id)
    if document is None or subject is None:
        raise LookupError("보험증권 또는 분석 대상자를 찾을 수 없습니다.")
    if not contract_group_fingerprint(document):
        raise ValueError("같은 계약으로 확인된 조회자료 묶음이 아닙니다.")
    members = _contract_group_members(session, document)
    if len(members) < 2:
        raise ValueError("함께 연결할 같은 계약 조회자료가 없습니다.")
    for member in members:
        member.subject = subject
    session.flush()
    return members


def subject_readiness(subject: AnalysisSubject | None) -> dict:
    blockers = []
    if subject is None:
        blockers.append("보험증권의 분석 대상자가 지정되지 않았습니다.")
    elif subject.review_status != "confirmed":
        blockers.append("분석 대상자 정보가 확인되지 않았습니다.")
    else:
        if subject.life_stage == "child":
            readiness = financial_readiness(subject.financial_profile, subject.life_stage)
            if not readiness["income_gap_ready"]:
                blockers.append("아동 분석에는 " + ", ".join(readiness["missing"]) + " 입력이 필요합니다.")
        if subject.life_stage == "retired_older_adult":
            readiness = financial_readiness(subject.financial_profile, subject.life_stage)
            if not readiness["income_gap_ready"]:
                blockers.append("노년기 분석에는 " + ", ".join(readiness["missing"]) + " 입력이 필요합니다.")
    return {"subject_ready": subject is not None and subject.review_status == "confirmed",
            "calculation_ready": not blockers, "blockers": blockers}
