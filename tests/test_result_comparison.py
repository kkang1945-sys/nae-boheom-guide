from insurance_app.result_comparison import compare_reports


def test_comparison_keeps_each_risk_separate_and_calculates_change():
    saved = {"scenarios": [{"risk_kind": "critical_illness", "standard_name": "일반암",
                              "status": "partial_gap", "gap_won": 5_000_000}]}
    current = {"scenarios": [{"risk_kind": "critical_illness", "standard_name": "일반암",
                                "status": "no_gap", "gap_won": 0},
                               {"risk_kind": "family_life", "standard_name": "질병사망",
                                "status": "pending"}]}
    report = compare_reports(saved, current)
    assert len(report["rows"]) == 2
    cancer = next(row for row in report["rows"] if row["standard_name"] == "일반암")
    assert cancer["gap_change_won"] == -5_000_000
    death = next(row for row in report["rows"] if row["standard_name"] == "질병사망")
    assert death["saved_status"] == "not_present"
    assert death["gap_change_won"] is None
