from types import SimpleNamespace

import insurance_app.subject_funding_results as results_module
from insurance_app.subject_funding_results import subject_funding_results


def profile(**changes):
    values = dict(monthly_essential_expenses_won=3_000_000, income_gap_months=6,
                  emergency_fund_to_use_won=3_000_000, paid_leave_support_won=0)
    values.update(changes)
    return SimpleNamespace(**values)


def row(row_id, amount, *, period="90세", frequency="once"):
    return SimpleNamespace(id=row_id, page_number=1, row_number=row_id, review_status="confirmed",
        confirmed_fields={"name": "일반암진단비", "amount_won": amount, "compensation_type": "fixed",
                          "insurance_period": period, "renewal": "nonrenewable",
                          "payment_frequency": frequency})


def document(doc_id, subject_id, rows):
    return SimpleNamespace(id=doc_id, subject_id=subject_id, source_name=f"{doc_id}.pdf", format="pdf",
                           pdf_coverages=rows, coverages=[])


def partial_preflight(*, metadata=True):
    return {"analysis_ready": False, "checks": [
        {"key": key, "passed": metadata if key == "metadata" else True}
        for key in ("subject", "life_stage", "metadata", "source")
    ]}


def test_results_keeps_different_diagnosis_types_separate(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    cancer = document(1, 7, [row(1, 10_000_000)])
    brain = document(2, 7, [row(2, 30_000_000)])
    brain.pdf_coverages[0].confirmed_fields["name"] = "뇌혈관질환진단비"
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [cancer, brain])

    assert {item["standard_name"] for item in report["scenarios"]} == {"일반암", "뇌혈관질환"}
    assert {item["confirmed_diagnosis_won"] for item in report["scenarios"]} == {10_000_000, 30_000_000}
    cancer_result = next(item for item in report["scenarios"] if item["standard_name"] == "일반암")
    assert cancer_result["priority_rank"] == 1
    assert cancer_result["review_amount_won"] == 5_000_000
    assert "부족해 보입니다" in cancer_result["headline"]
    assert "진단이 확정" in cancer_result["plain_explanation"]
    assert report["scenarios"][0] is cancer_result


def test_results_blocks_unready_or_duplicate_documents(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    first = document(1, 7, [row(1, 10_000_000)])
    second = document(2, 7, [row(2, 10_000_000)])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": False})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [
        {"document_ids": (1, 2)}])

    report = subject_funding_results(subject, [first, second])

    assert report["scenarios"] == []
    assert report["usable_document_count"] == 0
    assert len(report["blocked_documents"]) == 2


def test_results_keeps_disease_and_accident_death_separate(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile(loan_balance_won=0, dependent_count=1,
        family_monthly_expenses_won=2_000_000, family_support_months=6, liquid_assets_to_use_won=0))
    disease = document(1, 7, [row(1, 20_000_000)])
    accident = document(2, 7, [row(2, 30_000_000)])
    disease.pdf_coverages[0].confirmed_fields["name"] = "질병사망보험금"
    accident.pdf_coverages[0].confirmed_fields["name"] = "상해사망보험금"
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [disease, accident])

    assert {item["standard_name"] for item in report["scenarios"]} == {"질병사망", "상해사망"}
    assert all(item["risk_kind"] == "family_life" for item in report["scenarios"])
    assert all("사망" in item["headline"] for item in report["scenarios"])
    assert {item["runway"]["current_expense_months_after_debt"] for item in report["scenarios"]} == {10, 15}
    assert all(item["runway_public_reference"]["household_size"] == 1
               for item in report["scenarios"])


def test_compound_accidental_death_disability_row_uses_full_amount_for_death_only():
    coverage = row(9, 130_000_000)
    coverage.confirmed_fields["name"] = "일반상해사망후유장해추가담보"
    coverage.extracted_fields = {
        "description": "상해사고로 사망 또는 80%이상 후유장해시 가입금액 지급 "
                       "상해사고로 80%미만 후유장해시 가입금액×지급률 해당액 지급"
    }

    normalized, amount = results_module._normalized_protection(coverage)

    assert normalized.standard_name == "상해사망"
    assert amount == 130_000_000
    assert results_module._payout_conditions(coverage, "상해사망", amount) == [
        {"label": "상해사고로 사망 또는 80% 이상 후유장해", "amount_won": 130_000_000},
        {"label": "80% 미만 후유장해", "formula": "가입금액 × 약관의 장해 지급률"},
    ]


def test_medical_and_long_term_coverages_are_shown_without_amount_gap(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    medical = document(1, 7, [row(1, 10_000_000)])
    care = document(2, 7, [row(2, 10_000_000)])
    medical.pdf_coverages[0].confirmed_fields["name"] = "실손의료비"
    care.pdf_coverages[0].confirmed_fields["name"] = "간병보험금"
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [medical, care])

    assert report["scenarios"] == []
    assert {item["standard_name"] for item in report["protection_statuses"]} == {"실손의료비", "간병"}
    assert all(item["plain_explanation"] for item in report["protection_statuses"])


def test_single_nonfixed_or_unresolved_benefit_cannot_be_cash(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])
    for field, value in [('compensation_type', 'actual_loss'), ('renewal', 'unknown'),
                         ('payment_frequency', 'unknown'), ('payment_frequency', 'per_day'),
                         ('payment_frequency', 'repeat'), ('insurance_period', '')]:
        candidate = row(1, 10_000_000)
        candidate.confirmed_fields[field] = value
        report = subject_funding_results(subject, [document(1, 7, [candidate])])
        assert not report['scenarios'], (field, value)


def test_same_query_contract_benefits_are_never_counted_twice(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    first = document(1, 7, [row(1, 10_000_000)])
    second = document(2, 7, [row(2, 10_000_000)])
    fingerprint = "f" * 64
    for item in (first, second):
        item.metadata_evidence = {"query_layout": {
            "contract_group_fingerprint": fingerprint,
        }}
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [first, second])

    scenario = next(item for item in report["scenarios"] if item["standard_name"] == "일반암")
    assert scenario["status"] == "pending"
    assert "같은 계약" in scenario["reason"]
    assert "confirmed_diagnosis_won" not in scenario


def test_confirmed_risk_can_calculate_while_unrelated_row_is_pending(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    confirmed = row(1, 20_000_000)
    unrelated = row(2, 300_000)
    unrelated.confirmed_fields = None
    unrelated.extracted_fields = {"name": "골절진단위로금담보"}
    unrelated.review_status = "needs_review"
    candidate = document(1, 7, [confirmed, unrelated])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: partial_preflight())
    monkeypatch.setattr(results_module, "_common_conditions_ready", lambda _: True)
    monkeypatch.setattr(results_module, "_row_source_ready", lambda *_: (True, "확인"))
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate])

    assert report["usable_document_count"] == 1
    assert report["scenarios"][0]["standard_name"] == "일반암"
    assert report["scenarios"][0]["confirmed_diagnosis_won"] == 20_000_000


def test_partial_calculation_still_fails_closed_on_essential_gate(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    candidate = document(1, 7, [row(1, 20_000_000)])
    monkeypatch.setattr(results_module, "analysis_preflight",
                        lambda _: partial_preflight(metadata=False))
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate])

    assert report["usable_document_count"] == 0
    assert not report["scenarios"]


def test_partial_calculation_requires_resolved_common_conditions(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    candidate = document(1, 7, [row(1, 20_000_000)])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: partial_preflight())
    monkeypatch.setattr(results_module, "_common_conditions_ready", lambda _: False)
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate])

    assert report["usable_document_count"] == 0
    assert "공통 지급조건" in report["blocked_documents"][0]["reason"]


def test_unassigned_duplicate_candidate_does_not_block_subject_document(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    assigned = document(1, 7, [row(1, 20_000_000)])
    unassigned = document(2, None, [row(2, 20_000_000)])
    seen = []
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates",
                        lambda docs: seen.extend(docs) or [])

    report = subject_funding_results(subject, [assigned, unassigned])

    assert seen == [assigned]
    assert report["usable_document_count"] == 1
    assert report["scenarios"][0]["standard_name"] == "일반암"


def test_death_check_distinguishes_unconfirmed_candidate_from_not_found(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    cancer = row(1, 20_000_000)
    accident = row(2, 130_000_000)
    accident.review_status = "needs_review"
    accident.confirmed_fields = None
    accident.extracted_fields = {
        "name": "일반상해사망후유장해추가담보", "amount_won": 130_000_000,
    }
    candidate = document(1, 7, [cancer, accident])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: partial_preflight())
    monkeypatch.setattr(results_module, "_common_conditions_ready", lambda _: True)
    monkeypatch.setattr(results_module, "_row_source_ready", lambda *_: (True, "확인"))
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate])

    checks = {item["standard_name"]: item for item in report["coverage_checks"]}
    assert checks["상해사망"]["status"] == "needs_confirmation"
    assert checks["상해사망"]["candidate_amount_won"] == 130_000_000
    assert checks["질병사망"]["status"] == "not_found"
    assert checks["질병사망"]["candidate_amount_won"] is None


def test_confirmed_death_is_marked_calculated_not_missing(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile(
        loan_balance_won=0, dependent_count=1, family_monthly_expenses_won=2_000_000,
        family_support_months=6, liquid_assets_to_use_won=0))
    death = row(1, 20_000_000)
    death.confirmed_fields["name"] = "질병사망보험금"
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [document(1, 7, [death])])

    checks = {item["standard_name"]: item for item in report["coverage_checks"]}
    assert checks["질병사망"]["status"] == "calculated"
    assert checks["상해사망"]["status"] == "not_found"


def test_pending_death_scenario_is_not_repeated_as_a_candidate(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile(
        loan_balance_won=0, dependent_count=1, family_monthly_expenses_won=2_000_000,
        family_support_months=None, liquid_assets_to_use_won=0))
    death = row(1, 130_000_000)
    death.confirmed_fields["name"] = "상해사망보험금"
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [document(1, 7, [death])])

    assert report["scenarios"][0]["status"] == "pending"
    checks = {item["standard_name"]: item for item in report["coverage_checks"]}
    assert checks["상해사망"]["status"] == "calculated"


def test_transient_analysis_selects_document_without_persisted_subject_link(monkeypatch):
    subject = SimpleNamespace(id=-1, life_stage="working", financial_profile=profile())
    unassigned = document(2, None, [row(1, 20_000_000)])
    preflight = {"analysis_ready": False, "checks": [
        {"key": key, "passed": key not in {"subject", "life_stage"}}
        for key in ("subject", "life_stage", "metadata", "source")
    ]}
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: preflight)
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [unassigned], transient_document_ids={2})

    assert report["transient"] is True
    assert report["subject_document_count"] == 1
    assert report["scenarios"][0]["standard_name"] == "일반암"
    assert report["result_summary"]["critical_count"] == 1
    assert report["result_summary"]["runway_label"].startswith("약 ")
    assert "확인된 진단비와 비상자금으로 필수생활비를" in report["result_summary"]["headline"]


def test_confirmed_single_payment_hospital_benefit_calculates_selected_period(monkeypatch):
    hospital_profile = profile(
        hospitalization_months=2,
        hospital_monthly_maintained_income_won=1_000_000,
        hospital_additional_cost_won=500_000,
        hospital_paid_leave_support_won=500_000,
    )
    subject = SimpleNamespace(id=-1, life_stage="working", financial_profile=hospital_profile)
    hospital = row(1, 1_000_000)
    hospital.confirmed_fields["name"] = "입원급여금"
    candidate = document(1, None, [hospital])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate], transient_document_ids={1})

    result = next(item for item in report["scenarios"] if item["standard_name"] == "입원")
    assert result["risk_kind"] == "hospitalization"
    assert result["required_won"] == 6_500_000
    assert result["available_won"] == 6_500_000
    assert result["gap_won"] == 0
    assert result["confirmed_hospital_benefit_won"] == 1_000_000


def test_per_day_hospital_benefit_is_not_cash_and_is_summarized(monkeypatch):
    subject = SimpleNamespace(id=-1, life_stage="working", financial_profile=profile(
        hospitalization_months=2,
        hospital_monthly_maintained_income_won=0,
        hospital_additional_cost_won=0,
        hospital_paid_leave_support_won=0,
    ))
    hospital = row(1, 10_000, frequency="per_day")
    hospital.confirmed_fields["name"] = "입원급여금"
    candidate = document(1, None, [hospital])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate], transient_document_ids={1})

    result = next(item for item in report["scenarios"] if item["standard_name"] == "입원")
    assert result["confirmed_hospital_benefit_won"] == 0
    assert result["gap_won"] == 3_000_000
    assert result["no_confirmed_fixed_benefit"] is True
    assert [group["standard_name"] for group in report["other_coverage_groups"]] == ["입원"]
    assert report["other_coverage_groups"][0]["confirmation_status"] == "confirmed"
    assert report["other_coverage_groups"][0]["items"][0]["coverage_name"] == "입원급여금"


def test_confirmed_surgery_is_grouped_as_outside_current_calculation(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    surgery = row(1, 500_000)
    surgery.confirmed_fields["name"] = "질병수술비"
    candidate = document(1, 7, [surgery])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate])

    assert report["scenarios"] == []
    assert [group["standard_name"] for group in report["other_coverage_groups"]] == ["질병수술"]


def test_unconfirmed_coverage_candidate_is_labeled_and_never_calculated(monkeypatch):
    subject = SimpleNamespace(id=7, financial_profile=profile())
    surgery = row(1, 500_000)
    surgery.review_status = "needs_review"
    surgery.confirmed_fields = None
    surgery.extracted_fields = {"name": "질병수술비", "amount_won": 500_000}
    candidate = document(1, 7, [surgery])
    monkeypatch.setattr(results_module, "analysis_preflight", lambda _: {"analysis_ready": True})
    monkeypatch.setattr(results_module, "duplicate_contract_candidates", lambda _: [])

    report = subject_funding_results(subject, [candidate])

    assert report["scenarios"] == []
    group = report["other_coverage_groups"][0]
    assert group["standard_name"] == "질병수술"
    assert group["confirmation_status"] == "needs_confirmation"
