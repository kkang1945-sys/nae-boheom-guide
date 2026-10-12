from types import SimpleNamespace

from insurance_app.document_metadata import detect_metadata_candidates
from insurance_app.pdf_audit import _audit_query_rows, _query_amount_candidates
from insurance_app.policy_conditions import auto_check_document_scope, build_condition_evidence
from insurance_app.query_layout import LAYOUT, extract_query_layout, lines_from_words, query_metadata


SHA = "a" * 64


def word_line(y, *items):
    words = []
    for x, text in items:
        width = max(8, len(text) * 7)
        words.append([x, y, x + width, y + 9, text, 95.0])
    return words


def ocr(page_number, words):
    return SimpleNamespace(
        page_number=page_number,
        source_sha256=SHA,
        input_fingerprint=f"{page_number}" * 64,
        status="ocr_ready",
        text="\n".join(" ".join(str(word[4]) for word in line) for line in words),
        words=[word for line in words for word in line],
    )


def document(kind, page_words):
    paths = {
        "riders": "inquiry-special-contract",
        "contract": "inquiry-contract-detail",
        "benefits": "inquiry-guarantee-history",
    }
    pages = [SimpleNamespace(
        page_number=index,
        width=595.0,
        height=842.0,
        status="mixed_review",
        text=f"https://www.kyobo.com/dgt/web/insurance/{paths[kind]}",
    ) for index in range(1, len(page_words) + 1)]
    return SimpleNamespace(
        id=1,
        sha256=SHA,
        pages=pages,
        ocr_results=[ocr(index, words) for index, words in enumerate(page_words, 1)],
        pdf_coverages=[],
        pdf_runs=[],
        metadata_evidence=None,
    )


def identity_lines():
    return [
        word_line(80, (50, "무배당폰나이스암치료보험(만기)")),
        word_line(110, (50, "보험증권번호"), (180, "20205****960")),
    ]


def test_rider_query_layout_keeps_contract_summaries_out_of_payout_semantics():
    page_one = identity_lines() + [
        word_line(200, (50, "주계약")),
        word_line(230, (50, "가입금액"), (430, "2,000만원")),
        word_line(250, (50, "보험기간"), (430, "63년")),
        word_line(270, (50, "납입기간"), (430, "20년")),
        word_line(290, (50, "특약상태"), (430, "유지")),
        word_line(340, (50, "4대암치료특약")),
        word_line(370, (50, "가입금액"), (430, "1,000만원")),
        word_line(390, (50, "보험기간"), (430, "63년")),
        word_line(410, (50, "납입기간"), (430, "20년")),
        word_line(460, (50, "항암치료특약")),
        word_line(490, (50, "가입금액"), (430, "100만원")),
    ]
    page_two = [
        word_line(40, (50, "보험기간"), (430, "63년")),
        word_line(60, (50, "납입기간"), (430, "20년")),
        word_line(80, (50, "특약상태"), (430, "유지")),
        word_line(180, (42, "x="), (50, "양성종양수술특약")),
        word_line(210, (50, "가입금액"), (430, "100만원")),
        word_line(230, (50, "보험기간"), (430, "63년")),
        word_line(250, (50, "납입기간"), (430, "20년")),
        word_line(270, (50, "특약상태"), (430, "유지")),
    ]
    source = document("riders", [page_one, page_two])

    result = extract_query_layout(source)

    assert [row["fields"]["name"] for row in result.rows] == [
        "주계약", "4대암치료특약", "항암치료특약", "양성종양수술특약"
    ]
    assert [row["fields"]["amount_won"] for row in result.rows] == [
        20_000_000, 10_000_000, 1_000_000, 1_000_000
    ]
    assert all(row["fields"]["source_record_type"] == "contract_summary" for row in result.rows)
    assert all(row["fields"]["compensation_type"] == "unknown" for row in result.rows)
    assert result.rows[2]["fields"]["source_pages"] == [1, 2]


def test_benefit_query_layout_uses_spatial_amounts_and_explicit_frequencies():
    page = identity_lines() + [
        word_line(200, (50, "암진단자금")),
        word_line(230, (60, "보험기간중 최초 진단 확정시"), (445, "1회한"), (485, "20/000,000"), (550, "원")),
        word_line(300, (50, "암수술비")),
        word_line(330, (60, "암 치료를 목적으로 수술시"), (430, "수술"), (465, "1회당"), (505, "6,000,000")),
        word_line(400, (50, "암입원비")),
        word_line(430, (60, "4일이상 입원시"), (390, "3일초과"), (440, "1일당"), (480, "100,000"), (535, "원")),
    ]
    source = document("benefits", [page])

    result = extract_query_layout(source)

    assert [row["fields"]["amount_won"] for row in result.rows] == [20_000_000, 6_000_000, 100_000]
    assert [row["fields"]["payment_frequency"] for row in result.rows] == ["once", "per_event", "per_day"]
    assert all(row["fields"]["source_layout"] == LAYOUT for row in result.rows)


def test_benefit_query_layout_does_not_merge_frequency_digits_and_prefers_long_name():
    page = identity_lines() + [
        word_line(200, (50, "암진단자금")),
        word_line(230, (445, "1"), (455, "회한"), (485, "20/000,000"), (550, "원")),
        word_line(300, (50, "상피내암진단자금")),
        word_line(330, (445, "1회한"), (485, "4000,000"), (550, "원")),
        word_line(400, (50, "상피내암수술비")),
        word_line(430, (445, "수술1회당"), (495, "1200.000"), (550, "원")),
        word_line(500, (50, "상피내암입원비")),
        word_line(530, (410, "3일초과1일당"), (495, "20,/000"), (550, "원")),
    ]
    source = document("benefits", [page])

    result = extract_query_layout(source)

    assert [row["fields"]["name"] for row in result.rows] == [
        "암진단자금", "상피내암진단자금", "상피내암수술비", "상피내암입원비"
    ]
    assert [row["fields"]["amount_won"] for row in result.rows] == [
        20_000_000, 4_000_000, 1_200_000, 20_000
    ]


def test_rider_query_layout_tolerates_observed_label_and_unit_ocr_swaps():
    page = identity_lines() + [
        word_line(200, (50, "주계약")),
        word_line(230, (50, "가입금맥"), (250, "2.000"), (290, "만"), (310, "원")),
        word_line(250, (50, "보험기간"), (250, "63년")),
        word_line(270, (50, "납입기간"), (250, "20년")),
        word_line(330, (50, "항암치료특약")),
        word_line(360, (50, "가이그애"), (250, "100"), (290, "마"), (310, "위")),
        word_line(380, (50, "보험기간"), (250, "63년")),
        word_line(400, (50, "납입기간"), (250, "20년")),
    ]
    source = document("riders", [page])

    result = extract_query_layout(source)

    assert [row["fields"]["amount_won"] for row in result.rows] == [20_000_000, 1_000_000]
    assert not result.warnings


def test_query_audit_independently_handles_observed_money_ocr_punctuation():
    assert 20_000_000 in _query_amount_candidates("가입금맥 2.000 만 원")
    assert 1_000_000 in _query_amount_candidates("가이그애 100 마 위")
    assert 4_000_000 in _query_amount_candidates("1회한 4000,000 원")
    assert 1_200_000 in _query_amount_candidates("수술 1회당 1200.000 원")
    assert 20_000 in _query_amount_candidates("3일초과1일당 20,/000 원 120일 한도")


def test_query_lines_drop_malformed_embedded_tsv_payload_word():
    words = word_line(100, (50, "정상")) + [[100, 100, 200, 110, "MEARS\n5\t1\t1\t공", 90.0]]

    lines = lines_from_words(4, words)

    assert [line.text for line in lines] == ["정상"]


def test_masked_identifier_and_product_make_the_same_non_plaintext_contract_key():
    riders = document("riders", [identity_lines()])
    benefits = document("benefits", [identity_lines()])

    left = query_metadata(riders)
    right = query_metadata(benefits)

    assert left["contract_group_fingerprint"] == right["contract_group_fingerprint"]
    assert left["contract_group_fingerprint"] != "20205****960"
    assert left["contract_identifier_storage"] == "sha256_only"


def test_query_header_and_local_ocr_feed_unconfirmed_metadata_candidates():
    source = document("contract", [identity_lines()])
    source.insurer_candidate = None
    source.product_candidate = None

    evidence = detect_metadata_candidates(source)

    assert source.insurer_candidate == "교보생명"
    assert source.product_candidate == "무배당폰나이스암치료보험(만기)"
    assert evidence["insurer"]["source"] == "direct_web_inquiry_header"
    assert evidence["product"]["source"] == "local_ocr"
    assert evidence["query_layout"]["contract_group_fingerprint"]


def test_contract_query_layout_keeps_dates_and_periods_as_metadata_only():
    source = document("contract", [identity_lines() + [
        word_line(200, (50, "계약일"), (250, "2002.05.15")),
        word_line(230, (50, "보험기간"), (250, "63년")),
        word_line(260, (50, "납입기간"), (250, "20년")),
        word_line(290, (50, "보험만기일"), (250, "2065.05.15")),
        word_line(320, (50, "계약상태"), (250, "유지")),
    ]])

    result = extract_query_layout(source)

    assert result.rows == []
    assert result.metadata["contract_details"] == {
        "contract_date": "2002.05.15",
        "insurance_period": "63년",
        "payment_period": "20년",
        "maturity_date": "2065.05.15",
        "contract_status": "유지",
    }


def test_query_audit_recounts_titles_and_money_from_ocr_coordinates():
    page = identity_lines() + [
        word_line(200, (50, "암진단자금")),
        word_line(230, (60, "최초 진단 확정시"), (445, "1회한"), (485, "20,000,000"), (550, "원")),
    ]
    source = document("benefits", [page])
    extracted = extract_query_layout(source)
    source.pdf_coverages = [SimpleNamespace(
        id=index,
        page_number=row["page_number"],
        row_number=row["row_number"],
        extracted_fields=row["fields"],
        raw_cells=row["cells"],
        cell_boxes=row["boxes"],
        bbox=row["bbox"],
        confirmed_fields=None,
    ) for index, row in enumerate(extracted.rows, 1)]
    source.pdf_runs = [SimpleNamespace(warnings=[], row_count=1)]
    report = {"source_counts": {}, "rows": {}, "checked": 0, "unresolved": {}}
    issues = []

    _audit_query_rows(source, report, issues)

    assert issues == []
    assert report["checked"] == 1
    source.pdf_coverages[0].extracted_fields = {
        **source.pdf_coverages[0].extracted_fields, "amount_won": 2_000_000
    }
    report = {"source_counts": {}, "rows": {}, "checked": 0, "unresolved": {}}
    issues = []
    _audit_query_rows(source, report, issues)
    assert not report["rows"]["1"]["passed"]


def test_important_information_ocr_merges_split_fifty_percent_and_stops_at_riders():
    page = identity_lines() + [
        word_line(150, (50, "주요안내사항")),
        word_line(180, (60, "보험계약일로부터 2년 이내 암 또는 상피내암 진단시 진단자금 및 수술비 5")),
        word_line(195, (60, "0% 삭감 지급")),
        word_line(230, (60, "암 보 장 책 임 개시일 보험계약일 포함 90 일 경과 다음날")),
        word_line(300, (50, "보상대상"), (180, "가림")),
        word_line(330, (50, "특약명"), (180, "항암치료특약")),
    ]
    source = document("benefits", [page])
    evidence = build_condition_evidence(source)
    source.condition_evidence = evidence

    reduction = [item for item in evidence if item.category == "reduction"]
    assert any("50%" in item.source_text.replace(" ", "") for item in reduction)
    assert any(item.extracted_detail.get("scope_hint") == "주계약 암·상피내암 진단자금 및 수술비"
               for item in reduction)
    waiting = [item for item in evidence if item.category == "waiting_period"]
    assert len(waiting) == 1
    assert waiting[0].extracted_detail.get("scope_hint") == "주계약 암 관련 담보"
    changed = auto_check_document_scope(source)
    assert changed > 0
    assert all(item.review_status == "auto_checked" for item in evidence)
