from dataclasses import dataclass


STANDARD_COVERAGES = (
    "실손의료비",
    "일반암",
    "유사암",
    "뇌혈관질환",
    "뇌졸중",
    "허혈성심장질환",
    "급성심근경색",
    "질병수술",
    "상해수술",
    "입원",
    "후유장해",
    "간병",
    "치매",
    "질병사망",
    "상해사망",
)


@dataclass(frozen=True)
class NormalizedCoverage:
    original_name: str
    standard_name: str | None
    compensation_type: str
    benefit_type: str | None
    period: str
    renewal: bool
    payment_frequency: str
    confirmed: bool


def standardize_coverage_name(name: str) -> str | None:
    compact = "".join(name.split())
    # Names alone do not establish policy scope. Keep special cancer and
    # compound death/disability benefits out of an arbitrary first match.
    if any(word in compact for word in (
        "소액암", "특정암", "고액암", "재진단암", "이차암", "전이암", "3대암",
    )):
        return None
    if "사망" in compact and "후유장해" in compact:
        return None
    rules = (
        (("실손", "실손의료"), "실손의료비"),
        (("유사암",), "유사암"),
        (("뇌혈관",), "뇌혈관질환"),
        (("뇌졸중",), "뇌졸중"),
        (("허혈성심장",), "허혈성심장질환"),
        (("급성심근경색",), "급성심근경색"),
        (("질병사망",), "질병사망"),
        (("상해사망",), "상해사망"),
        (("질병수술",), "질병수술"),
        (("상해수술",), "상해수술"),
        (("후유장해",), "후유장해"),
        (("간병",), "간병"),
        (("치매",), "치매"),
        (("입원",), "입원"),
        (("일반암",), "일반암"),
    )
    matches = {standard for keywords, standard in rules
               if any(keyword in compact for keyword in keywords)}
    if "암진단" in compact.replace("유사암", ""): matches.add("일반암")
    # Hospitalization describes the setting of an actual-cost benefit.
    if "실손의료비" in matches:
        matches.discard("입원")
    # Public health-policy terms commonly put the hospital setting before a
    # caregiver daily benefit. The actual benefit remains care, not admission.
    if "간병" in matches and ("사용일당" in compact or "간호·간병통합서비스" in compact):
        matches.discard("입원")
    # An explicit exclusion is not a second positive cancer benefit.
    if "유사암제외" in compact:
        matches.discard("유사암")
    return next(iter(matches)) if len(matches) == 1 else None


def can_combine(left: NormalizedCoverage, right: NormalizedCoverage) -> tuple[bool, str]:
    if not left.confirmed or not right.confirmed:
        return False, "사용자가 확인하지 않은 담보는 합산하지 않습니다."
    if left.compensation_type != "fixed" or right.compensation_type != "fixed":
        return False, "실손형 담보는 가입금액을 단순 합산하지 않습니다."
    if left.standard_name is None or left.standard_name != right.standard_name:
        return False, "보장 범위가 같은 표준 담보가 아닙니다."
    if left.benefit_type is None or left.benefit_type != right.benefit_type:
        return False, "진단·수술·입원 등 지급 종류가 다릅니다."
    if left.period != right.period or left.renewal != right.renewal:
        return False, "보험기간 또는 갱신 조건이 다릅니다."
    if left.payment_frequency == "unknown" or left.payment_frequency != right.payment_frequency:
        return False, "지급 횟수 조건이 다르거나 확인되지 않았습니다."
    return True, "동일 조건의 확정 정액형 담보입니다."

