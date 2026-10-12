from types import SimpleNamespace

from insurance_app.contract_duplicates import duplicate_contract_candidates, same_contract_groups


def row(name, amount, period):
    return SimpleNamespace(extracted_name=name, extracted_amount_won=amount, extracted_period=period)


def pdf_row(name, amount, period):
    return SimpleNamespace(extracted_fields={"name": name, "amount_won": amount, "raw_period": period})


def test_high_row_overlap_is_flagged_but_never_merged():
    values = [(f"담보 {index}", index * 10000, "20년납90세만기") for index in range(1, 7)]
    hwp = SimpleNamespace(id=1, format="hwpml", product_candidate="안심보험",
                          coverages=[row(*value) for value in values], pdf_coverages=[])
    pdf = SimpleNamespace(id=2, format="pdf", product_candidate="안심보험",
                          coverages=[], pdf_coverages=[pdf_row(*value) for value in values])
    result = duplicate_contract_candidates([hwp, pdf])
    assert result == [{"document_ids": (1, 2), "shared_rows": 6, "overlap": 1,
                       "product_match": True, "status": "possible_duplicate", "action": "do_not_combine"}]


def test_small_or_different_sets_are_not_called_duplicates():
    one = SimpleNamespace(id=1, format="hwpml", product_candidate=None,
                          coverages=[row("A", 1, "1년")], pdf_coverages=[])
    two = SimpleNamespace(id=2, format="pdf", product_candidate=None,
                          coverages=[], pdf_coverages=[pdf_row("B", 2, "2년")])
    assert duplicate_contract_candidates([one, two]) == []


def test_query_documents_with_same_hashed_identity_are_one_contract_group():
    fingerprint = "f" * 64
    one = SimpleNamespace(id=4, format="pdf", product_candidate="확인보험",
        coverages=[], pdf_coverages=[], metadata_evidence={"query_layout": {
            "contract_group_fingerprint": fingerprint}})
    two = SimpleNamespace(id=6, format="pdf", product_candidate="확인보험",
        coverages=[], pdf_coverages=[], metadata_evidence={"query_layout": {
            "contract_group_fingerprint": fingerprint}})

    assert duplicate_contract_candidates([one, two]) == [{
        "document_ids": (4, 6), "shared_rows": 0, "overlap": None,
        "product_match": True, "status": "same_contract_group",
        "action": "treat_as_one_contract",
        "match_basis": "masked_contract_identifier_and_product",
    }]
    assert same_contract_groups([two, one]) == [[one, two]]
