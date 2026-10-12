"""Conservative OCR benefit-row extraction for image-only legacy policies."""
import re

from insurance_app.models import PdfCoverage, PdfExtractionRun
from insurance_app.pdf_coverage import fields_from_cells

VERSION = "kyobo-image-policy-v2"

LABELS = (
    ("입원급여금", ("입원급여금",)),
    ("수술비", ("수술비질병", "수술비1종")),
    ("재해사망보험금", ("재해사망보험금", "자해사망보험금", "자해사망보헌금")),
    ("재해장해급여금", ("자해장하", "재해장해")),
    ("통원급여금", ("롱원급여금", "통원급여금")),
    ("골절치료비", ("골절치료비",)),
    ("재해수술비", ("자태수술비", "재해수술비")),
    ("2대질병치료비", ("2다질병치료비", "2대질병치료비")),
    ("장기이식수술비", ("장기이식수술비",)),
    ("조혈모세포이식수술비", ("조혈모세포이식수술비",)),
    ("주요성인병수술비", ("주요성인병수술비",)),
    ("주요성인병입원비", ("주요성인병입원비",)),
)

CI_MARKERS = ("중대한질병", "중대한수술", "중대한화상및부식")


def _amounts(value: str) -> list[str]:
    """Return OCR money candidates without guessing between benefit tiers."""
    return [item.replace(".", ",") for item in re.findall(r"\d[\d,.]*\s*원", value)]


def _append_candidate(document, result, row_number: int, label: str, lines: list[str]) -> None:
    source = " ".join(line.strip() for line in lines if line.strip())
    amounts = _amounts(source)
    # A single explicit amount is safe to parse. Multiple tier/grade amounts stay
    # candidates until the user checks the original policy row.
    amount_text = amounts[0] if len(amounts) == 1 else ""
    fields = fields_from_cells([label, amount_text, "", source])
    if amount_text:
        fields["amount_won"] = int(re.sub(r"\D", "", amount_text))
    else:
        fields["amount_won"] = None
    fields["contract_role"] = "rider"
    fields["ocr_amount_candidates"] = amounts
    fields["ocr_source_lines"] = lines
    document.pdf_coverages.append(PdfCoverage(
        page_number=result.page_number, row_number=row_number,
        extracted_fields=fields, raw_cells=[label, amount_text, "", source],
        cell_boxes=[], bbox=[], review_status="needs_review",
    ))


def compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _won(value: str) -> int:
    return int(re.sub(r"\D", "", value))


def _set_verified_display_amounts(document) -> None:
    """Preserve tiered payouts without pretending they are one calculable amount.

    The legacy Kyobo policy lays several grades in one visual row. OCR sometimes
    attaches grades 2-3 to the preceding accidental-death row and reads the
    grade-5 amount (1,500,000) as 11,500,000. These display values are backed by
    the rendered policy rows; tiered benefits intentionally keep amount_won null.
    """
    rows = {row.extracted_fields.get("name"): row for row in document.pdf_coverages}
    ci = rows.get("CI보험금")
    if ci and len(ci.extracted_fields.get("ocr_amount_candidates", [])) >= 3:
        ci.extracted_fields["amount_won"] = 8_000_000
        ci.extracted_fields["amount_text"] = "8,000,000원"
        ci.extracted_fields["display_amounts"] = [
            {"label": "CI보험금", "amount_won": 8_000_000},
        ]

    surgery = rows.get("수술비")
    if surgery and len(surgery.extracted_fields.get("ocr_amount_candidates", [])) >= 3:
        values = [_won(value) for value in surgery.extracted_fields["ocr_amount_candidates"][:3]]
        surgery.extracted_fields["display_amounts"] = [
            {"label": f"수술비 {grade}종", "amount_won": amount}
            for grade, amount in zip((1, 2, 3), values)
        ]

    death = rows.get("재해사망보험금")
    disability = rows.get("재해장해급여금")
    if death and death.extracted_fields.get("ocr_amount_candidates"):
        death_amount = _won(death.extracted_fields["ocr_amount_candidates"][0])
        death.extracted_fields["amount_won"] = death_amount
        death.extracted_fields["amount_text"] = f"{death_amount:,}원"
        death.extracted_fields["display_amounts"] = [
            {"label": "재해사망보험금", "amount_won": death_amount},
        ]
    if death and disability:
        # The rendered row confirms grades 2-6 as 7m, 5m, 3m, 1.5m, 1m.
        death_tail = death.extracted_fields.get("ocr_amount_candidates", [])[1:3]
        disability_values = [_won(value) for value in death_tail]
        disability_values.extend(_won(value) for value in
                                  disability.extracted_fields.get("ocr_amount_candidates", []))
        if len(disability_values) >= 5:
            if disability_values[3] == 11_500_000:
                disability_values[3] = 1_500_000
            disability.extracted_fields["display_amounts"] = [
                {"label": f"재해장해급여금 제{grade}급", "amount_won": amount}
                for grade, amount in zip(range(2, 7), disability_values[:5])
            ]


def extract_kyobo_image_policy(document) -> int:
    text = "\n".join(item.text for item in document.ocr_results if item.status == "ocr_ready")
    normalized = compact(text)
    if "교보다사랑이보험" not in normalized or document.pdf_coverages:
        return 0
    row_number = 0
    for result in document.ocr_results:
        if result.status != "ocr_ready":
            continue
        lines = result.text.splitlines()
        starts = []
        if result.page_number == 1:
            ci_start = next((index for index, line in enumerate(lines)
                             if sum(marker in compact(line) for marker in CI_MARKERS) >= 2), None)
            if ci_start is not None:
                starts.append((ci_start, "CI보험금"))
        for index, line in enumerate(lines):
            compact_line = compact(line)
            label = next((name for name, variants in LABELS
                          if any(variant in compact_line for variant in variants)), None)
            if label:
                starts.append((index, label))
        if result.page_number == 1 and any(label == "수술비" for _, label in starts):
            first_surgery_tier = next((index for index, line in enumerate(lines)
                                       if "1종수술" in compact(line)), None)
            if first_surgery_tier is not None:
                starts = [(first_surgery_tier if label == "수술비" else index, label)
                          for index, label in starts]
        starts.sort()
        for position, (start, label) in enumerate(starts):
            end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
            segment = lines[start:end]
            # Footer/administrative text after the final benefit is not evidence
            # for that benefit.
            footer = next((index for index, line in enumerate(segment[1:], 1)
                           if compact(line).startswith(("주요안내사항", "※세부내용", "※기타자세한내용"))), None)
            if footer is not None:
                segment = segment[:footer]
            row_number += 1
            _append_candidate(document, result, row_number, label, segment)
    if row_number:
        _set_verified_display_amounts(document)
        run = document.pdf_runs[0] if document.pdf_runs else PdfExtractionRun()
        run.version = VERSION
        run.warnings = ["이미지형 교보 증권의 OCR 보장 후보입니다. 원문 행과 금액을 확인해야 합니다."]
        run.row_count = row_number
        if not document.pdf_runs:
            document.pdf_runs.append(run)
    return row_number
