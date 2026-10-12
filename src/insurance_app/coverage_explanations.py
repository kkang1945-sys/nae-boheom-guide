"""Plain-language descriptions of standard groups, separate from contract terms."""

from insurance_app.coverage_rules import STANDARD_COVERAGES, standardize_coverage_name

VERSION = "plain-language-coverage-v1"

EXPLANATIONS = {
    "실손의료비": "실제로 본인이 부담한 의료비 중 약관에서 정한 범위를 보상하는 담보입니다.",
    "일반암": "약관에서 정한 일반암으로 진단이 확정됐을 때 지급하는 진단 담보입니다.",
    "유사암": "약관에서 정한 유사암으로 진단이 확정됐을 때 지급하는 담보입니다.",
    "뇌혈관질환": "약관에서 정한 뇌혈관질환으로 진단이 확정됐을 때 지급하는 담보입니다.",
    "뇌졸중": "약관에서 정한 뇌졸중으로 진단이 확정됐을 때 지급하는 담보입니다.",
    "허혈성심장질환": "약관에서 정한 허혈성심장질환으로 진단이 확정됐을 때 지급하는 담보입니다.",
    "급성심근경색": "약관에서 정한 급성심근경색증으로 진단이 확정됐을 때 지급하는 담보입니다.",
    "질병수술": "질병 때문에 약관에서 정한 수술을 받았을 때 지급하는 담보입니다.",
    "상해수술": "사고로 다쳐 약관에서 정한 수술을 받았을 때 지급하는 담보입니다.",
    "입원": "입원 치료 시 약관에서 정한 조건과 기간에 따라 지급하거나 보상하는 담보입니다.",
    "후유장해": "사고나 질병 뒤 남은 장해가 약관의 지급 기준에 해당할 때 지급하는 담보입니다.",
    "간병": "간병이 필요해 약관에서 정한 상태와 조건에 해당할 때 지급하는 담보입니다.",
    "치매": "치매 진단이나 상태가 약관에서 정한 기준에 해당할 때 지급하는 담보입니다.",
    "질병사망": "질병으로 사망했을 때 약관에서 정한 수익자에게 지급하는 담보입니다.",
    "상해사망": "사고로 사망했을 때 약관에서 정한 수익자에게 지급하는 담보입니다.",
}


def plain_coverage_explanation(name: str) -> str:
    """Short, non-advisory explanation for a single extracted coverage name."""
    standard = standardize_coverage_name(name)
    if standard in EXPLANATIONS:
        return EXPLANATIONS[standard]
    compact = "".join((name or "").split())
    if "CI보험금" in compact:
        return "증권에서 정한 중대한 질병·수술·화상 등의 조건에 해당할 때 지급되는 보장입니다."
    if "이식" in compact:
        return "증권에서 정한 장기 또는 조혈모세포 이식수술을 받았을 때 지급되는 보장입니다."
    if "골절" in compact:
        return "재해로 골절이 발생해 증권의 지급조건에 해당할 때 지급되는 보장입니다."
    if "통원" in compact:
        return "증권에서 정한 질병이나 사고로 통원 치료를 받을 때 지급되는 보장입니다."
    if "입원" in compact:
        return "질병이나 사고로 일정 기간 이상 입원했을 때 일수와 한도에 따라 지급되는 보장입니다."
    if "수술" in compact:
        return "질병이나 사고로 증권에서 정한 수술을 받았을 때 지급되는 보장입니다."
    if "장해" in compact:
        return "사고 뒤 장해가 남아 증권의 장해등급 기준에 해당할 때 지급되는 보장입니다."
    if "사망" in compact:
        return "증권에서 정한 사망 원인과 조건에 해당할 때 수익자에게 지급되는 보장입니다."
    if "질병" in compact or "진단" in compact:
        return "증권에서 정한 질병으로 진단이 확정되고 지급조건에 해당할 때 지급되는 보장입니다."
    return "증권에 적힌 지급 사유와 조건에 해당할 때 사용할 수 있는 보장입니다."


def explanation_groups(groups):
    result = []
    for group in groups:
        if group["name"] not in STANDARD_COVERAGES:
            continue
        result.append({**group, "plain_explanation": EXPLANATIONS[group["name"]]})
    return result
