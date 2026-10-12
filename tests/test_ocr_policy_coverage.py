from insurance_app.models import InsuranceDocument, OcrPageResult
from insurance_app.ocr_policy_coverage import extract_kyobo_image_policy
from insurance_app.policy_conditions import build_condition_evidence


def test_extracts_kyobo_image_policy_rows_as_unconfirmed_candidates():
    document = InsuranceDocument(source_name="kyobo.pdf", source_path="kyobo.pdf",
                                 sha256="x", file_size=1, format="pdf")
    document.ocr_results.append(OcrPageResult(
        document_id=1, page_number=1, source_sha256="x", input_fingerprint="x",
        engine_version="test", languages="kor", dpi=300, words=[], status="ocr_ready",
        text=("무 배 당 교 보 다 사 랑 이 보 험\n"
              "입 원 급여금 질병 또는 재해로 4일 이상 입원시 3일초과1일당 10,000원\n"
              "2 다 질 병 치료비 뇌출혈 또는 급성심근경색증 진단확정시 10,000,000원")))
    count = extract_kyobo_image_policy(document)
    assert count == 2
    assert [row.extracted_fields["name"] for row in document.pdf_coverages] == ["입원급여금", "2대질병치료비"]
    assert [row.extracted_fields["amount_won"] for row in document.pdf_coverages] == [10_000, 10_000_000]
    assert all(row.review_status == "needs_review" for row in document.pdf_coverages)


def test_groups_multiline_tiered_benefits_for_display_without_summing_tiers():
    document = InsuranceDocument(source_name="kyobo.pdf", source_path="kyobo.pdf",
                                 sha256="x", file_size=1, format="pdf")
    document.ocr_results.append(OcrPageResult(
        document_id=1, page_number=1, source_sha256="x", input_fingerprint="x",
        engine_version="test", languages="kor", dpi=300, words=[], status="ocr_ready",
        text=("무 배 당 교 보 다 사 랑 이 보 험\n"
              "중대한 질병 중대한 수술 또는 중대한 화상 및 부식\n"
              "보험금 이미 지급되었을 경우 2,000,000원\n"
              "보험금이 지급되지 않았을 경우 10,000,000원\n"
              "최초 진단 또는 수술시 8,000,000원\n"
              "입원급여금 3일초과 1일당 10,000원\n"
              "수술비 1종 수술 200,000원\n"
              "2종 수술 500,000원\n"
              "3종 수술 1,000,000원")))
    assert extract_kyobo_image_policy(document) == 3
    ci, admission, surgery = document.pdf_coverages
    assert ci.extracted_fields["ocr_amount_candidates"] == ["2,000,000원", "10,000,000원", "8,000,000원"]
    assert ci.extracted_fields["amount_won"] == 8_000_000
    assert ci.extracted_fields["display_amounts"] == [
        {"label": "CI보험금", "amount_won": 8_000_000}
    ]
    assert admission.extracted_fields["amount_won"] == 10_000
    assert surgery.extracted_fields["ocr_amount_candidates"] == ["200,000원", "500,000원", "1,000,000원"]
    assert surgery.extracted_fields["amount_won"] is None
    assert surgery.extracted_fields["display_amounts"] == [
        {"label": "수술비 1종", "amount_won": 200_000},
        {"label": "수술비 2종", "amount_won": 500_000},
        {"label": "수술비 3종", "amount_won": 1_000_000},
    ]


def test_separates_accidental_death_from_disability_grades_for_display():
    document = InsuranceDocument(source_name="kyobo.pdf", source_path="kyobo.pdf",
                                 sha256="x", file_size=1, format="pdf")
    document.ocr_results.append(OcrPageResult(
        document_id=1, page_number=1, source_sha256="x", input_fingerprint="x",
        engine_version="test", languages="kor", dpi=300, words=[], status="ocr_ready",
        text=("무 배 당 교 보 다 사 랑 이 보 험\n"
              "재해사망보험금 재해로 사망 또는 제1급 장해시 10,000,000원\n"
              "제2급 7,000,000원\n제3급 5,000,000원\n"
              "재해장해급여금 제2급-제6급 장해시\n"
              "제4급 3,000,000원\n제5급 11,500,000원\n제6급 1,000,000원")))
    assert extract_kyobo_image_policy(document) == 2
    death, disability = document.pdf_coverages
    assert death.extracted_fields["amount_won"] == 10_000_000
    assert disability.extracted_fields["amount_won"] is None
    assert disability.extracted_fields["display_amounts"] == [
        {"label": "재해장해급여금 제2급", "amount_won": 7_000_000},
        {"label": "재해장해급여금 제3급", "amount_won": 5_000_000},
        {"label": "재해장해급여금 제4급", "amount_won": 3_000_000},
        {"label": "재해장해급여금 제5급", "amount_won": 1_500_000},
        {"label": "재해장해급여금 제6급", "amount_won": 1_000_000},
    ]


def test_moves_first_surgery_tier_out_of_admission_row():
    document = InsuranceDocument(source_name="kyobo.pdf", source_path="kyobo.pdf",
                                 sha256="x", file_size=1, format="pdf")
    document.ocr_results.append(OcrPageResult(
        document_id=1, page_number=1, source_sha256="x", input_fingerprint="x",
        engine_version="test", languages="kor", dpi=300, words=[], status="ocr_ready",
        text=("무 배 당 교 보 다 사 랑 이 보 험\n"
              "입 원 급여금 3일초과 1일당 10,000원\n"
              "1 종 수 술 200,000원\n"
              "수술비 질병 또는 재해의 치료를 목적으로 수술시 2종 수술 500,000원\n"
              "3종 수술 1,000,000원")))
    assert extract_kyobo_image_policy(document) == 2
    admission, surgery = document.pdf_coverages
    assert admission.extracted_fields["amount_won"] == 10_000
    assert surgery.extracted_fields["ocr_amount_candidates"] == ["200,000원", "500,000원", "1,000,000원"]


def test_recognizes_actual_ocr_spelling_for_accidental_death():
    document = InsuranceDocument(source_name="kyobo.pdf", source_path="kyobo.pdf",
                                 sha256="x", file_size=1, format="pdf")
    document.ocr_results.append(OcrPageResult(
        document_id=1, page_number=1, source_sha256="x", input_fingerprint="x",
        engine_version="test", languages="kor", dpi=300, words=[], status="ocr_ready",
        text=("무 배 당 교 보 다 사 랑 이 보 험\n"
              "자 해 사 망 보 헌금 자 해 를 원 인 으 로 사 망 시 10,000,000원")))
    assert extract_kyobo_image_policy(document) == 1
    assert document.pdf_coverages[0].extracted_fields["name"] == "재해사망보험금"
    # OCR-only candidates do not have reliable geometric boxes, but condition
    # evidence generation must remain safe.
    build_condition_evidence(document)
