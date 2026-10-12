from types import SimpleNamespace

from insurance_app.portfolio_integration import portfolio_integration


def coverage(coverage_id, name, amount, **changes):
    fields = {"name": name, "amount_won": amount, "insurance_period": "90세만기",
              "renewal": "nonrenewable", "contract_role": "rider",
              "compensation_type": "fixed", "payment_frequency": "once"} | changes
    return SimpleNamespace(id=coverage_id, page_number=1, row_number=coverage_id,
                           extracted_fields=fields, confirmed_fields=fields,
                           review_status="confirmed")


def document(document_id, rows, *, subject_id=10):
    return SimpleNamespace(id=document_id, subject_id=subject_id, format="pdf",
                           source_name=f"policy-{document_id}.pdf", product_candidate=None,
                           pdf_coverages=rows, coverages=[])


def test_sums_only_different_confirmed_fixed_documents_with_same_terms():
    subject = SimpleNamespace(id=10)
    first = document(1, [coverage(1, "일반암진단담보", 10_000_000)])
    second = document(2, [coverage(2, "암진단급여금담보", 20_000_000)])
    report = portfolio_integration(subject, [first, second])
    assert report["groups"][0]["standard_name"] == "일반암"
    assert report["groups"][0]["nominal_total_won"] == 30_000_000
    assert len(report["groups"][0]["items"]) == 2


def test_never_sums_one_document_actual_cost_or_different_terms():
    subject = SimpleNamespace(id=10)
    one = document(1, [coverage(1, "일반암진단담보", 10_000_000), coverage(2, "암진단급여금담보", 20_000_000)])
    two = document(2, [coverage(3, "상해입원실손의료담보", 30_000_000, compensation_type="actual_cost")])
    assert not portfolio_integration(subject, [one, two])["groups"]


def test_different_subject_documents_do_not_enter_portfolio():
    subject = SimpleNamespace(id=10)
    first = document(1, [coverage(1, "일반암진단담보", 10_000_000)])
    other_subject = document(2, [coverage(2, "암진단급여금담보", 20_000_000)], subject_id=11)
    report = portfolio_integration(subject, [first, other_subject])
    assert report["subject_document_count"] == 1
    assert not report["groups"]


def test_same_query_contract_group_is_not_counted_as_two_insurances():
    subject = SimpleNamespace(id=10)
    fingerprint = "f" * 64
    first = document(1, [coverage(1, "일반암진단담보", 10_000_000)])
    second = document(2, [coverage(2, "암진단급여금담보", 20_000_000)])
    for item in (first, second):
        item.metadata_evidence = {"query_layout": {
            "contract_group_fingerprint": fingerprint}}

    report = portfolio_integration(subject, [first, second])

    assert report["groups"] == []
    assert report["grouped_document_ids"] == [1, 2]


def test_contract_summary_amount_never_enters_nominal_payout_total():
    subject = SimpleNamespace(id=10)
    summary = coverage(1, "일반암진단담보", 10_000_000,
                       source_record_type="contract_summary")
    benefit = coverage(2, "암진단급여금담보", 20_000_000)

    report = portfolio_integration(subject, [document(1, [summary]), document(2, [benefit])])

    assert report["groups"] == []
