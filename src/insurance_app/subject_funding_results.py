"""Fail-closed funding-gap scenarios for one analysis subject."""
from __future__ import annotations

from insurance_app.analysis_preflight import analysis_preflight
from insurance_app.contract_duplicates import contract_key, duplicate_contract_candidates
from insurance_app.coverage_rules import NormalizedCoverage, can_combine, standardize_coverage_name
from insurance_app.funding_gap import (
    death_coverage_runway, family_life_gap, family_life_required, income_gap, income_gap_scenarios,
)
from insurance_app.coverage_explanations import EXPLANATIONS
from insurance_app.ocr import current_ocr
from insurance_app.pdf_audit import current_audit
from insurance_app.portfolio_integration import BENEFIT_TYPES
from insurance_app.public_living_standard import minimum_living_reference

VERSION = "subject-funding-results-v9"
DIAGNOSIS_STANDARDS = {
    "일반암", "유사암", "뇌혈관질환", "뇌졸중", "허혈성심장질환", "급성심근경색",
}
DEATH_STANDARDS = {"질병사망", "상해사망"}
STATUS_STANDARDS = {"실손의료비", "후유장해", "간병", "치매"}
HOSPITAL_STANDARD = "입원"
ESSENTIAL_DOCUMENT_CHECKS = {"subject", "life_stage", "metadata", "source"}
READY_EVIDENCE_STATUSES = {"confirmed", "auto_checked"}
READY_COMMON_STATUSES = READY_EVIDENCE_STATUSES | {"not_applicable"}
DEATH_COVERAGE_CHECKS = (
    ("질병사망", "일반·질병 사망"),
    ("상해사망", "상해 사망"),
)


def _plain_result(standard: str, risk_kind: str, result: dict) -> dict:
    """Add concise, non-sales guidance without changing the calculation."""
    if result["status"] == "pending":
        return {
            "headline": f"{standard} 보장은 확인이 더 필요합니다.",
            "guidance": "확정되지 않은 조건은 부족액 계산에 넣지 않았습니다.",
            "plain_explanation": EXPLANATIONS.get(standard, "증권의 지급 조건을 확인해야 하는 보장입니다."),
        }
    gap = result["gap_won"]
    if risk_kind == "family_life":
        event = "질병 사망" if standard == "질병사망" else "사고 사망"
        target = "부채·가족생활 자금"
    elif risk_kind == "hospitalization":
        event = "입원·휴직"
        target = "생활자금"
    else:
        event = f"{standard} 발생"
        target = "생활자금"
    if gap:
        headline = f"{event}에 따른 {target}이 부족해 보입니다."
        guidance = f"현재 입력 기준 자금공백 {gap:,}원을 우선 검토해 보세요."
    else:
        headline = f"{event}에 따른 계산상 자금공백은 없습니다."
        guidance = "현재 입력과 확인된 보장 기준 결과이며, 실제 지급 조건은 원문에서 확인해야 합니다."
    return {
        "headline": headline,
        "guidance": guidance,
        "review_amount_won": gap,
        "plain_explanation": EXPLANATIONS.get(standard, "증권의 지급 조건을 확인해야 하는 보장입니다."),
    }


def _prioritize(scenarios: list[dict]) -> list[dict]:
    ordered = sorted(scenarios, key=lambda item: (
        item.get("status") == "pending",
        item.get("gap_won", 0) == 0,
        -item.get("gap_won", 0),
        item["standard_name"],
    ))
    rank = 0
    for item in ordered:
        if item.get("status") != "pending" and item.get("gap_won", 0) > 0:
            rank += 1
            item["priority_rank"] = rank
    return ordered


def _death_coverage_checks(usable_documents, calculated_standards: set[str]) -> list[dict]:
    """Expose missing vs unresolved death cover without treating candidates as cash."""
    candidates: dict[str, list[dict]] = {standard: [] for standard, _ in DEATH_COVERAGE_CHECKS}
    for document in usable_documents:
        for row in document.pdf_coverages:
            fields = row.confirmed_fields or row.extracted_fields or {}
            name = fields.get("name", "")
            standard = standardize_coverage_name(name)
            compact_name = "".join(name.split())
            # A compound death/disability name is intentionally not normalized
            # as a payable death benefit. It is still useful as a review candidate.
            if standard is None and "질병사망" in compact_name:
                standard = "질병사망"
            elif standard is None and "상해사망" in compact_name:
                standard = "상해사망"
            if standard not in candidates:
                continue
            amount = fields.get("amount_won")
            candidates[standard].append({
                "document_id": document.id,
                "document_name": document.source_name,
                "coverage_id": row.id,
                "coverage_name": fields.get("name", "확인 필요 담보"),
                "page_number": row.page_number,
                "row_number": row.row_number,
                "candidate_amount_won": amount if isinstance(amount, int) and amount >= 0 else None,
                "review_status": row.review_status,
            })

    checks = []
    for standard, label in DEATH_COVERAGE_CHECKS:
        if standard in calculated_standards:
            status = "calculated"
            headline = f"{label} 보장은 확인된 금액으로 계산했습니다."
        elif candidates[standard]:
            status = "needs_confirmation"
            headline = f"{label} 관련 담보 후보가 있지만 아직 확정되지 않았습니다."
        else:
            status = "not_found"
            headline = f"업로드한 증권에서 확인된 {label} 보장을 찾지 못했습니다."
        amounts = [item["candidate_amount_won"] for item in candidates[standard]
                   if item["candidate_amount_won"] is not None]
        checks.append({
            "standard_name": standard,
            "label": label,
            "status": status,
            "headline": headline,
            "candidate_amount_won": sum(amounts) if amounts else None,
            "items": candidates[standard],
            "scope": ("후보 가입금액은 이름과 금액 칸에서 읽은 값일 뿐 지급조건이 확정된 보험금이 아닙니다. "
                      "확정 전에는 자금공백 계산에 사용하지 않습니다."),
        })
    return checks


def _normalized_protection(row):
    if getattr(row, "extracted_fields", {}).get("source_record_type") == "contract_summary":
        return None
    fields = row.confirmed_fields or {}
    standard = standardize_coverage_name(fields.get("name", ""))
    original = getattr(row, "extracted_fields", {}) or {}
    compact_name = "".join(fields.get("name", "").split())
    compact_description = "".join(original.get("description", "").split())
    # A single source row can explicitly promise the full 가입금액 both for
    # accidental death and for severe disability. Use the full amount for the
    # death scenario only; the percentage-based sub-80% disability remains out
    # of monetary calculation until its grade/rate is known.
    if (standard is None and "상해사망" in compact_name and "후유장해" in compact_name
            and "사망또는80%이상후유장해시가입금액지급" in compact_description):
        standard = "상해사망"
    amount = fields.get("amount_won")
    if (row.review_status != "confirmed" or standard not in DIAGNOSIS_STANDARDS | DEATH_STANDARDS | STATUS_STANDARDS | {HOSPITAL_STANDARD}
            or not isinstance(amount, int) or amount < 0):
        return None
    if standard in DIAGNOSIS_STANDARDS | DEATH_STANDARDS and (
        fields.get("compensation_type") != "fixed"
        or fields.get("renewal") not in {"renewable", "nonrenewable"}
        or fields.get("payment_frequency") not in {"once"}
        or not fields.get("insurance_period", "").strip()
    ):
        return None
    if standard == HOSPITAL_STANDARD and (
        fields.get("compensation_type") != "fixed"
        or fields.get("payment_frequency") != "once"
        or not fields.get("insurance_period", "").strip()
    ):
        return None
    normalized = NormalizedCoverage(
        original_name=fields["name"], standard_name=standard,
        compensation_type=fields.get("compensation_type", "unknown"),
        benefit_type=BENEFIT_TYPES.get(standard), period=fields.get("insurance_period", ""),
        renewal=fields.get("renewal") == "renewable",
        payment_frequency=fields.get("payment_frequency", "unknown"), confirmed=True,
    )
    return normalized, amount


def _payout_conditions(row, standard: str, amount: int) -> list[dict]:
    """Return source-backed display conditions without inventing a payout."""
    name = "".join(((row.confirmed_fields or {}).get("name", "")).split())
    description = "".join(((getattr(row, "extracted_fields", {}) or {}).get("description", "")).split())
    if (standard == "상해사망" and "사망" in name and "후유장해" in name
            and "사망또는80%이상후유장해시가입금액지급" in description):
        return [
            {"label": "상해사고로 사망 또는 80% 이상 후유장해", "amount_won": amount},
            {"label": "80% 미만 후유장해", "formula": "가입금액 × 약관의 장해 지급률"},
        ]
    return []


def _essential_document_ready(preflight: dict) -> bool:
    checks = {item["key"]: item["passed"] for item in preflight.get("checks", [])}
    return all(checks.get(key, False) for key in ESSENTIAL_DOCUMENT_CHECKS)


def _common_conditions_ready(document) -> bool:
    common = [item for item in document.condition_evidence if item.coverage_id is None]
    return bool(common) and all(item.review_status in READY_COMMON_STATUSES for item in common)


def _row_source_ready(document, row) -> tuple[bool, str]:
    audit = current_audit(document)
    if not audit or not audit.get("rows", {}).get(str(row.id), {}).get("passed"):
        return False, "해당 담보의 이름·금액·기간 원문 검산이 필요합니다."
    page = next((page for page in document.pages if page.page_number == row.page_number), None)
    if page is None:
        return False, "해당 담보의 원문 페이지를 찾을 수 없습니다."
    # A mixed-layout page can still contain the complete text used by the
    # row-level audit. Require OCR only when there is no extracted page text.
    if not (page.text or "").strip():
        ocr = current_ocr(document, row.page_number)
        if ocr is None or ocr.status != "ocr_ready":
            return False, "해당 담보가 있는 페이지의 문자 인식 확인이 필요합니다."
    benefit = [item for item in document.condition_evidence
               if item.coverage_id == row.id and item.category == "benefit_text"]
    if not benefit or any(item.review_status not in READY_EVIDENCE_STATUSES for item in benefit):
        return False, "해당 담보의 보장내용 원문 근거 확인이 필요합니다."
    return True, "해당 담보의 원문 근거가 확인되었습니다."


def _hospitalization_gap(profile, confirmed_benefit_won: int) -> dict:
    months = getattr(profile, "hospitalization_months", 0) or 0
    if months <= 0:
        return {
            "status": "pending", "reason": "입원·휴직 기간을 선택하지 않아 생활자금 계산을 생략했습니다.",
            "missing": ["입원·휴직 기간"],
        }
    monthly_expense = getattr(profile, "monthly_essential_expenses_won", None)
    if not isinstance(monthly_expense, int) or monthly_expense <= 0:
        return {"status": "pending", "reason": "월 필수생활비 확인이 필요합니다.", "missing": ["월 필수생활비"]}
    maintained_income = getattr(profile, "hospital_monthly_maintained_income_won", 0) or 0
    extra_cost = getattr(profile, "hospital_additional_cost_won", 0) or 0
    paid_support = getattr(profile, "hospital_paid_leave_support_won", 0) or 0
    emergency = getattr(profile, "emergency_fund_to_use_won", 0) or 0
    required = monthly_expense * months + extra_cost
    available = maintained_income * months + paid_support + emergency + confirmed_benefit_won
    gap = max(required - available, 0)
    return {
        "status": "gap" if gap else "no_gap",
        "hospitalization_months": months,
        "required_won": required,
        "available_won": available,
        "gap_won": gap,
        "confirmed_hospital_benefit_won": confirmed_benefit_won,
        "monthly_essential_expenses_won": monthly_expense,
        "monthly_maintained_income_won": maintained_income,
        "additional_cost_won": extra_cost,
        "paid_leave_support_won": paid_support,
        "emergency_fund_won": emergency,
        "formula": "(월 필수생활비 × 입원·휴직 개월 수) + 추가비용 - (유지소득 × 개월 수) - 유급휴가·회사 지원 - 사용할 비상자금 - 확인된 정액형 입원급여",
        "scope": "사용자가 선택한 기간의 생활자금 모의계산입니다. 실손형 입원의료비, 일당형 담보와 지급조건 미확정 금액은 합산하지 않았습니다.",
    }


def _other_coverage_groups(documents, used_coverage_ids: set[int]) -> list[dict]:
    """Group uncalculated protection candidates without implying a shortage."""
    groups: dict[str, list[dict]] = {}
    for document in documents:
        for row in document.pdf_coverages:
            if row.id in used_coverage_ids:
                continue
            fields = row.confirmed_fields or row.extracted_fields or {}
            name = fields.get("name", "").strip()
            standard = standardize_coverage_name(name)
            if standard is None and "치아" in "".join(name.split()):
                standard = "치아"
            if not standard:
                continue
            groups.setdefault(standard, []).append({
                "document_id": document.id, "document_name": document.source_name,
                "coverage_id": row.id, "coverage_name": name,
                "page_number": row.page_number,
                "review_status": row.review_status,
            })
    return [{
        "standard_name": standard,
        "plain_explanation": EXPLANATIONS.get(standard, "현재 생활자금 계산과 지급 기준이 달라 별도로 확인하는 보장입니다."),
        "confirmation_status": ("confirmed" if all(item["review_status"] == "confirmed" for item in items)
                                else "needs_confirmation"),
        "items": items,
    } for standard, items in sorted(groups.items())]


def subject_funding_results(subject, documents, *, transient_document_ids=None) -> dict:
    """Return one independent scenario per diagnosis type; never add different risks."""
    transient_ids = set(transient_document_ids or ())
    transient = transient_document_ids is not None
    subject_documents = ([doc for doc in documents if doc.id in transient_ids] if transient
                         else [doc for doc in documents if doc.subject_id == subject.id])
    duplicate_ids = {doc_id for report in duplicate_contract_candidates(subject_documents)
                     if report.get("action", "do_not_combine") == "do_not_combine"
                     for doc_id in report["document_ids"]}
    blocked_documents = []
    usable = []
    fully_ready: dict[int, bool] = {}
    for document in subject_documents:
        if document.format != "pdf":
            blocked_documents.append({"document_id": document.id, "document_name": document.source_name,
                                      "reason": "PDF 담보표가 아니어서 이 계산에 사용하지 않았습니다."})
        elif document.id in duplicate_ids:
            blocked_documents.append({"document_id": document.id, "document_name": document.source_name,
                                      "reason": "같은 계약의 다른 파일일 수 있어 계산에서 제외했습니다."})
        else:
            preflight = analysis_preflight(document)
            if transient:
                preflight = dict(preflight)
                preflight["checks"] = [
                    item | {"passed": True, "message": "이번 모의계산에서만 입력한 대상자·경제정보"}
                    if item["key"] in {"subject", "life_stage"} else item
                    for item in preflight.get("checks", [])
                ]
                preflight["analysis_ready"] = all(item["passed"] for item in preflight["checks"])
            if not preflight["analysis_ready"] and not _essential_document_ready(preflight):
                blocked_documents.append({"document_id": document.id, "document_name": document.source_name,
                                          "reason": "분석 대상자·경제조건·보험회사·상품명 또는 원본 연결 확인이 남아 있습니다.",
                                          "preflight": preflight})
            elif not preflight["analysis_ready"] and not _common_conditions_ready(document):
                blocked_documents.append({"document_id": document.id, "document_name": document.source_name,
                                          "reason": "문서 전체에 적용될 수 있는 공통 지급조건의 범위 확인이 남아 있습니다.",
                                          "preflight": preflight})
            else:
                usable.append(document)
                fully_ready[document.id] = preflight["analysis_ready"]

    by_standard = {}
    for document in usable:
        per_document = {}
        for row in document.pdf_coverages:
            result = _normalized_protection(row)
            source_ready, source_reason = ((True, "문서 전체 확인 완료")
                                           if fully_ready[document.id]
                                           else _row_source_ready(document, row))
            if result is not None and source_ready:
                per_document.setdefault(result[0].standard_name, []).append((row, *result))
            else:
                blocked_documents.append({"document_id": document.id, "document_name": document.source_name,
                    "reason": (f"{(row.confirmed_fields or row.extracted_fields or {}).get('name', '확인 필요 담보')}: "
                               + (source_reason if result is not None else
                                  "지급 조건이 확정되지 않았거나 현재 자금공백 계산 대상이 아니어서 금액 계산에서 제외했습니다."))})
        for standard, entries in per_document.items():
            if len(entries) != 1:
                blocked_documents.append({"document_id": document.id, "document_name": document.source_name,
                                          "reason": f"{standard} 담보가 한 문서에 여러 개여서 지급 조건을 구분하기 전에는 계산하지 않았습니다."})
                continue
            by_standard.setdefault(standard, []).append((document, *entries[0]))

    scenarios = []
    protection_statuses = []
    for standard, entries in sorted(by_standard.items()):
        if standard in STATUS_STANDARDS:
            protection_statuses.append({
                "standard_name": standard,
                "kind": "medical" if standard == "실손의료비" else "long_term",
                "plain_explanation": EXPLANATIONS[standard],
                "items": [{
                    "document_id": document.id, "document_name": document.source_name,
                    "coverage_id": row.id, "coverage_name": normalized.original_name,
                    "page_number": row.page_number, "row_number": row.row_number,
                } for document, row, normalized, _ in entries],
                "scope": "실손은 가입금액처럼 차감하지 않으며, 후유장해·간병·치매는 반복 지급과 세부 범위를 확인하기 전에는 금액 공백을 계산하지 않습니다.",
            })
            continue
        if len({contract_key(document) for document, *_ in entries}) < len(entries):
            scenarios.append({
                "standard_name": standard,
                "status": "pending",
                "reason": "같은 계약의 여러 조회자료에 같은 위험 담보가 있어 중복 여부를 구분하기 전에는 합산하지 않았습니다.",
            })
            continue
        base = entries[0][2]
        comparable = all(can_combine(base, entry[2])[0] for entry in entries[1:])
        if not comparable:
            scenarios.append({"standard_name": standard, "status": "pending",
                              "reason": "서로 다른 보험의 지급 조건이 같다고 확인되지 않아 합산하지 않았습니다."})
            continue
        amount = sum(entry[3] for entry in entries)
        risk_kind = ("critical_illness" if standard in DIAGNOSIS_STANDARDS else
                     "hospitalization" if standard == HOSPITAL_STANDARD else "family_life")
        life_stage = getattr(subject, "life_stage", None)
        profile = subject.financial_profile
        if risk_kind == "critical_illness":
            result = income_gap(profile, amount, life_stage=life_stage)
        elif risk_kind == "hospitalization":
            result = _hospitalization_gap(profile, amount)
        else:
            result = family_life_gap(profile, amount)
        if risk_kind == "critical_illness":
            result["duration_scenarios"] = income_gap_scenarios(
                profile, amount, life_stage=life_stage
            )
            monthly_expense = getattr(profile, "monthly_essential_expenses_won", None)
            if transient and isinstance(monthly_expense, int) and monthly_expense > 0:
                usable_money = amount + (getattr(profile, "emergency_fund_to_use_won", 0) or 0)
                result["runway_months"] = usable_money // monthly_expense
                result["runway_money_won"] = usable_money
        else:
            public_reference = minimum_living_reference(getattr(profile, "dependent_count", None))
            result["runway"] = death_coverage_runway(
                confirmed_death_won=amount,
                loan_balance_won=getattr(profile, "loan_balance_won", None),
                monthly_family_expenses_won=getattr(profile, "family_monthly_expenses_won", None),
                public_monthly_reference_won=(public_reference or {}).get("monthly_won"),
            )
            result["runway_public_reference"] = public_reference
        result.update({"standard_name": standard, "risk_kind": risk_kind,
                       "reference_amount_won": amount, "items": [
            {"document_id": document.id, "document_name": document.source_name,
             "coverage_id": row.id, "coverage_name": normalized.original_name,
             "page_number": row.page_number, "row_number": row.row_number, "amount_won": value,
             "payout_conditions": _payout_conditions(row, standard, value)}
            for document, row, normalized, value in entries
        ]})
        result.update(_plain_result(standard, risk_kind, result))
        scenarios.append(result)

    if (transient and (getattr(subject.financial_profile, "hospitalization_months", 0) or 0) > 0
            and not any(item.get("standard_name") == HOSPITAL_STANDARD for item in scenarios)):
        result = _hospitalization_gap(subject.financial_profile, 0)
        result.update({
            "standard_name": HOSPITAL_STANDARD,
            "risk_kind": "hospitalization",
            "reference_amount_won": 0,
            "items": [],
            "no_confirmed_fixed_benefit": True,
        })
        result.update(_plain_result(HOSPITAL_STANDARD, "hospitalization", result))
        scenarios.append(result)

    # A scenario already owns the display for that risk even when its personal
    # funding-gap calculation is pending. Do not repeat it as an unresolved
    # candidate in the family-living-cost section.
    calculated_standards = {item["standard_name"] for item in scenarios}
    coverage_checks = _death_coverage_checks(usable, calculated_standards) if usable else []
    used_coverage_ids = {
        item["coverage_id"]
        for result in scenarios + protection_statuses
        for item in result.get("items", [])
    }
    other_coverage_groups = _other_coverage_groups(subject_documents, used_coverage_ids)
    fallback = {"critical_illness": income_gap(
                    subject.financial_profile, None, life_stage=getattr(subject, "life_stage", None)),
                "family_life": family_life_gap(subject.financial_profile, None)}
    ranked_scenarios = _prioritize(scenarios)
    ready_critical = [
        item for item in ranked_scenarios
        if item.get("risk_kind") == "critical_illness"
        and isinstance(item.get("runway_months"), int)
    ]
    runway_values = [item["runway_months"] for item in ready_critical]
    if runway_values:
        runway_range = (f"약 {runway_values[0]}개월" if len(set(runway_values)) == 1
                        else f"약 {min(runway_values)}~{max(runway_values)}개월")
        result_headline = f"확인된 진단비와 비상자금으로 필수생활비를 {runway_range} 감당할 수 있습니다."
    else:
        runway_range = "계산 전"
        result_headline = "확인된 보장과 생활비를 기준으로 결과를 보여드립니다."
    return {
        "version": VERSION, "transient": transient,
        "subject_document_count": len(subject_documents),
        "documents": [{"id": document.id, "name": document.source_name}
                      for document in subject_documents],
        "usable_document_count": len(usable), "blocked_documents": blocked_documents,
        "scenarios": ranked_scenarios, "fallback": fallback,
        "result_summary": {
            "headline": result_headline,
            "runway_label": runway_range,
            "critical_count": len(ready_critical),
            "critical_scenarios": ready_critical,
        },
        "protection_statuses": protection_statuses, "coverage_checks": coverage_checks,
        "other_coverage_groups": other_coverage_groups,
        "family_life_need": ({"status": "pending", "missing": ["준비기간을 묻지 않아 금액 공백은 계산하지 않습니다"],
            "scope": "준비기간을 묻지 않고 부채와 생활비를 각각 보여줍니다."}
            if transient else family_life_required(subject.financial_profile)),
        "scope": "질병 종류가 다른 진단비는 합산하지 않았습니다. 각 결과는 입력한 생활비 시나리오이며 실제 보험금 지급 여부를 판단하지 않습니다.",
    }
