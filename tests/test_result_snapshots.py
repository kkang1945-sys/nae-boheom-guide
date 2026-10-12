from sqlalchemy import select

from insurance_app.database import create_schema, create_session_factory
from insurance_app.models import AnalysisResultSnapshot
from insurance_app.financial_profiles import save_financial_profile
from insurance_app.result_snapshots import save_result_snapshot
from insurance_app.subjects import save_subject


def test_snapshot_keeps_result_and_reproducible_fingerprint(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    report = {"version": "subject-funding-results-v1", "scenarios": [], "scope": "test"}
    with factory.begin() as session:
        subject = save_subject(session, label="나", relationship_to_user="self", birth_year=1990,
            life_stage="working", economic_role="primary_earner", sex_specific_review="not_applicable",
            pregnancy_childbirth_review="not_applicable")
        first = save_result_snapshot(session, subject, report)
        second = save_result_snapshot(session, subject, report)
        assert first.input_fingerprint == second.input_fingerprint
    with factory() as session:
        saved = list(session.scalars(select(AnalysisResultSnapshot)))
        assert len(saved) == 2
        assert saved[0].report["scenarios"] == report["scenarios"]
        assert saved[0].report["subject_label"] == "나"
        assert "evidence" in saved[0].report
        assert "evidence" not in report


def test_snapshot_keeps_retirement_inputs_and_stage_formula(tmp_path):
    factory = create_session_factory(tmp_path / "test.sqlite3")
    create_schema(factory)
    report = {"version": "subject-funding-results-v3", "scenarios": [], "scope": "test"}
    with factory.begin() as session:
        subject = save_subject(session, label="부모님", relationship_to_user="parent", birth_year=1950,
            life_stage="retired_older_adult", economic_role="retired", sex_specific_review="not_applicable",
            pregnancy_childbirth_review="not_applicable")
        save_financial_profile(session, subject, {
            "income_gap_months": "6", "emergency_fund_to_use_won": "0",
            "monthly_essential_expenses_won": "2000000",
            "retirement_monthly_income_to_use_won": "1200000",
            "retirement_monthly_care_cost_won": "300000",
        })
        snapshot = save_result_snapshot(session, subject, report)
        inputs = {item["key"]: item["value"] for item in snapshot.report["evidence"]["inputs"]}
        assert inputs["retirement_monthly_income_to_use_won"] == 1_200_000
        assert "노년기 생활자금" in snapshot.report["evidence"]["formulae"][0]
