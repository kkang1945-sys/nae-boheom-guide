"""Curated public-system references used only as user-checkable context."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone


VERIFIED_ON = "2026-09-08"

_PUBLIC_PROGRAMS = (
    {
        "id": "copayment_ceiling",
        "title": "본인부담상한제",
        "institution": "국민건강보험공단",
        "summary": "연간 건강보험 본인일부부담금이 개인별 상한을 넘으면 초과분을 공단이 부담하는 제도입니다.",
        "caveat": "비급여, 선별급여 등 제외 항목이 있고 개인별 상한과 적용 여부가 다르므로 공단에서 확인해야 합니다.",
        "source_title": "국민건강보험공단 본인부담상한제 안내",
        "url": "https://www.nhis.or.kr/static/html/wbma/c/wbmac0209.html",
        "verified_on": VERIFIED_ON,
        "audience": "all",
    },
    {
        "id": "special_copayment",
        "title": "본인일부부담금 산정특례",
        "institution": "국민건강보험공단",
        "summary": "등록된 중증질환 등의 건강보험 급여 진료에 대해 본인부담을 낮추는 제도입니다.",
        "caveat": "질환·등록·적용기간과 급여 여부에 따라 달라지며 비급여 등은 적용되지 않을 수 있습니다.",
        "source_title": "국민건강보험공단 산정특례 안내",
        "url": "https://www.nhis.or.kr/static/html/wbma/c/wbmac0215.html",
        "verified_on": VERIFIED_ON,
        "audience": "all",
    },
    {
        "id": "catastrophic_medical_expense",
        "title": "재난적의료비 지원사업",
        "institution": "국민건강보험공단·보건복지부",
        "summary": "소득·재산과 의료비 부담 등을 심사해 과도한 의료비의 일부를 지원하는 제도입니다.",
        "caveat": "지원 대상·제외 비용·다른 지원 또는 보험금과의 관계는 신청 시점의 기준과 개별 심사로 확인해야 합니다.",
        "source_title": "재난적의료비 지원을 위한 기준 등에 관한 고시",
        "url": "https://www.nhis.or.kr/lm/lmxsrv/law/lawFullContent.do?SEQ=1726&SEQ_HISTORY=613880",
        "verified_on": VERIFIED_ON,
        "audience": "all",
    },
    {
        "id": "long_term_care",
        "title": "노인장기요양보험 급여",
        "institution": "국민건강보험공단",
        "summary": "장기요양 인정을 받은 수급자에게 인정 내용과 이용계획에 따라 재가·시설 등의 급여를 제공하는 제도입니다.",
        "caveat": "인정등급, 급여 종류, 이용계획과 본인부담에 따라 실제 이용 범위와 비용이 달라집니다.",
        "source_title": "국민건강보험공단 노인장기요양보험 안내",
        "url": "https://www.nhis.or.kr/static/html/wbda/c/wbdac01.html",
        "verified_on": VERIFIED_ON,
        "audience": "older_or_geriatric_condition",
    },
    {
        "id": "infant_health_screening",
        "title": "영유아 건강검진",
        "institution": "국민건강보험공단",
        "summary": "영유아의 성장·발달을 정해진 시기에 확인하는 국가 건강검진입니다.",
        "caveat": "출생일과 월령별 검진 시기, 정해진 검진 항목에 해당하는지 공단에서 확인해야 합니다.",
        "source_title": "국민건강보험공단 영유아 건강검진 안내",
        "url": "https://www.nhis.or.kr/static/html/wbma/c/wbhaca04800_2024.pdf",
        "verified_on": VERIFIED_ON,
        "audience": "infant",
    },
    {
        "id": "pregnancy_childbirth_medical",
        "title": "건강보험 임신·출산 진료비 지원",
        "institution": "국민건강보험공단",
        "summary": "임신·출산과 영유아 진료의 본인부담에 사용할 수 있는 이용권 제도입니다.",
        "caveat": "임신·출산 확인, 건강보험 자격, 사용기간과 사용범위를 공단에서 확인해야 합니다.",
        "source_title": "국민건강보험공단 임신·출산 진료비 제도",
        "url": "https://www.nhis.or.kr/static/html/wbma/c/wbmac0212.html",
        "verified_on": VERIFIED_ON,
        "audience": "pregnancy",
    },
    {
        "id": "national_pension_old_age",
        "title": "국민연금 노령연금",
        "institution": "국민연금공단",
        "summary": "가입기간과 출생연도별 지급개시연령 등의 요건을 충족하면 매월 지급되는 공적연금입니다.",
        "caveat": "가입기간, 지급개시연령, 소득활동과 개인별 예상액을 국민연금공단에서 확인해야 합니다.",
        "source_title": "국민연금공단 노령연금 안내",
        "url": "https://www.nps.or.kr/pnsinfo/ntpsklg/getOHAF0056M0.do",
        "verified_on": VERIFIED_ON,
        "audience": "retirement",
    },
)


def public_program_evidence(subject=None) -> list[dict[str, str]]:
    """Return general or subject-relevant references without deciding eligibility."""
    programs = deepcopy(_PUBLIC_PROGRAMS)
    if subject is None:
        for item in programs:
            item["relevance"] = "전체 제도 목록"
            item["review_reason"] = "대상자별 결과에서는 생애단계와 선택 항목에 맞춰 우선순위를 표시합니다."
        return programs
    age = datetime.now(timezone.utc).year - subject.birth_year if subject.birth_year is not None else None
    result = []
    for item in programs:
        audience = item["audience"]
        reason = None
        if audience == "all":
            reason = "연령·성별과 별개로 의료비 부담이 생기면 적용 가능성을 확인합니다."
        elif audience == "older_or_geriatric_condition" and (
            subject.life_stage in {"retirement_transition", "retired_older_adult"}
            or (age is not None and age >= 65)
        ):
            reason = "노년·은퇴 단계이므로 장기요양 인정 대상과 실제 본인부담을 우선 확인합니다."
        elif audience == "infant" and subject.life_stage == "child" and (age is None or age <= 7):
            reason = "출생연도상 영유아 검진 시기에 해당할 수 있어 정확한 생일과 검진 차수를 확인합니다."
        elif audience == "pregnancy" and subject.pregnancy_childbirth_review == "include":
            reason = "임신·출산 관련 담보 검토를 선택했으므로 이용권 적용 여부를 함께 확인합니다."
        elif audience == "retirement" and subject.life_stage in {"retirement_transition", "retired_older_adult"}:
            reason = "은퇴 전환 또는 노년 단계이므로 실제 수령액을 지속소득 입력과 대조합니다."
        if reason:
            item["relevance"] = "공통 확인" if audience == "all" else "생애단계 우선 확인"
            item["review_reason"] = reason
            result.append(item)
    return result


PUBLIC_EVIDENCE_SCOPE = (
    "공적제도는 적용 가능성을 직접 확인하기 위한 참고정보입니다. 예상 지원금을 계산하거나 자금공백에서 차감하지 않으며, "
    "정부·공공기관이 개인별 민간보험 가입금액을 권장한다는 뜻이 아닙니다."
)
