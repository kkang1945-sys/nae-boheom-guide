"""Source-bound checks. A pass verifies transcription only, never payout eligibility."""
from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
from pathlib import Path

import pdfplumber
import pymupdf

from insurance_app.models import PdfAudit
from insurance_app.ocr_policy_coverage import VERSION as KYOBO_IMAGE_VERSION
from insurance_app.query_layout import LAYOUT as QUERY_LAYOUT

VERSION = f'transcription-v6/pdfplumber-{pdfplumber.__version__}/pymupdf-{pymupdf.VersionBind}'
SEMANTICS = ('renewal', 'contract_role', 'compensation_type', 'payment_frequency')


def normalized(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value or ''))


def independent_won(value):
    """Strict integer grammar, independent of the extraction amount converter."""
    text = normalized(value)
    if not text.endswith('원'):
        return None
    text = text[:-1]
    # Commas must be thousands separators within each numeric token.
    for number in re.findall(r'[\d,]+', text):
        if ',' in number and not re.fullmatch(r'[1-9]\d{0,2}(,\d{3})+', number):
            return None
    text = text.replace(',', '')
    if not text or not re.fullmatch(r'[0-9억만천백십]+', text):
        return None
    total, section, number, last_small, last_large = 0, 0, '', 10000, 10**12
    for char in text:
        if char.isdigit():
            number += char
            continue
        unit = {'십':10, '백':100, '천':1000, '만':10000, '억':100000000}[char]
        if unit < 10000:
            if unit >= last_small:
                return None
            section += int(number or '1') * unit
            last_small = unit
        else:
            if unit >= last_large:
                return None
            coefficient = section + int(number or '0') if section or number else 1
            total += coefficient * unit
            section, last_small, last_large = 0, 10000, unit
        number = ''
    result = total + section + int(number or '0')
    return result if 0 <= result <= 9_000_000_000_000_000 else None


def snapshot(document):
    try:
        content = Path(document.source_path).read_bytes()
    except OSError:
        content = None
    digest = hashlib.sha256(content).hexdigest() if content is not None else None
    payload = dict(version=VERSION, source=digest, expected=document.sha256,
        pages=[dict(number=p.page_number, width=p.width, height=p.height, rotation=p.rotation,
                    text=p.text, words=p.words, status=p.status, reader=p.reader_version) for p in document.pages],
        rows=[dict(id=c.id, page=c.page_number, row=c.row_number, fields=c.extracted_fields,
                   cells=c.raw_cells, boxes=c.cell_boxes, bbox=c.bbox, confirmed=c.confirmed_fields,
                   status=c.review_status) for c in document.pdf_coverages],
        runs=[dict(version=r.version, warnings=r.warnings, count=r.row_count) for r in document.pdf_runs],
        ocr=[dict(page=o.page_number, source=o.source_sha256, fingerprint=o.input_fingerprint,
                  status=o.status, text=o.text, words=o.words) for o in document.ocr_results])
    fingerprint = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return fingerprint, digest, content


def current_audit(document):
    if document.format != 'pdf':
        return None
    fingerprint, _, _ = snapshot(document)
    return next((a.report for a in reversed(document.pdf_audits)
                 if a.version == VERSION and a.input_fingerprint == fingerprint), None)


def source_amounts(page):
    """Find table headers and enumerate amounts without using primary row boxes."""
    patterns = [r'담\s*보\s*명', r'가\s*입\s*금\s*액', r'납\s*기\s*및\s*만\s*기', r'보\s*장\s*내\s*용']
    hits = [page.search(pattern) or [] for pattern in patterns]
    headers = []
    for amount in hits[1]:
        aligned = [[h for h in group if abs(h['top'] - amount['top']) < 3] for group in hits]
        if all(len(group) == 1 for group in aligned):
            xs = [group[0]['x0'] for group in aligned]
            if xs == sorted(xs):
                headers.append(amount)
    if not headers:
        return None
    if len(headers) != 1:
        raise ValueError('한 페이지에 여러 담보 표가 있어 추가 확인이 필요합니다.')
    header = headers[0]
    footers = [h['top'] for h in (page.search(r'추\s*가\s*정\s*보\s*사\s*항') or []) if h['top'] > header['bottom']]
    bottom = min(footers, default=page.height)
    # This supported layout centers the amount heading above its values.
    crop = page.crop((max(0, header['x0']-10), header['bottom'], min(page.width, header['x1']+10), bottom))
    return crop.search(r'[0-9][0-9,억천백십만\s]*원') or []


def source_table_rows(page):
    """Independently locate either supported table layout with pdfplumber."""
    four = ['담보명', '가입금액', '납기및만기', '보장내용']
    six = ['담보명', '', '보험시기', '보험종기', '가입금액(만원)', '보장내용상세(지급조건)']
    matched = []
    settings = {"vertical_strategy": "lines", "horizontal_strategy": "lines"}
    for table in page.extract_tables(settings):
        if not table:
            continue
        header = [normalized(cell) for cell in table[0]]
        if header in (four, six):
            rows = [row for row in table[1:] if any(normalized(cell) for cell in row)]
            if header == six:
                rows = [row for row in rows if normalized(row[4]) or normalized(row[5])]
            matched.append((len(header), rows))
    if not matched:
        return None
    if len(matched) != 1:
        raise ValueError('한 페이지에 여러 담보 표가 있어 추가 확인이 필요합니다.')
    return matched[0]


def independent_source_row_count(page, rows) -> int | None:
    """Count source rows without relying on the primary extractor's row boxes."""
    table = source_table_rows(page)
    if table is not None:
        return len(table[1])
    # Some supported four-column PDFs expose clear headers and amount-column
    # values but pdfplumber does not reconstruct the ruled grid as a table.
    if rows and all(len(row.raw_cells) == 4 for row in rows):
        amounts = source_amounts(page)
        if amounts is not None:
            return len(amounts)
    return None


_QUERY_BENEFIT_NAMES = (
    "암진단자금", "암수술비", "암입원비", "암간병자금",
    "상피내암진단자금", "상피내암수술비", "상피내암입원비", "상피내암간병자금",
)
_QUERY_RIDER_NAMES = ("주계약", "4대암치료특약", "항암치료특약", "양성종양수술특약")


def _inside_query_box(word, box):
    center = ((float(word[0]) + float(word[2])) / 2, (float(word[1]) + float(word[3])) / 2)
    return box[0] <= center[0] <= box[2] and box[1] <= center[1] <= box[3]


def _query_amount_candidates(text):
    compact_text = normalized(text).replace(",/", ",").replace("/，", ",")
    compact_text = compact_text.replace("/", ",").replace(".", ",")
    values = set()
    for match in re.finditer(
        r"(?<![\d,])(\d{1,4}(?:,\d{3})+)(?P<unit>만원|만[원위워]|마[원위워]|원)?(?![\d,])",
        compact_text,
    ):
        amount = int(match.group(1).replace(",", ""))
        if (match.group("unit") or "").startswith(("만", "마")):
            amount *= 10_000
        values.add(amount)
    for match in re.finditer(r"(?<!\d)(\d{1,6})(?:만원|만[원위워]|마[원위워])(?!\d)", compact_text):
        values.add(int(match.group(1)) * 10_000)
    return values


def _query_term_count(text, term):
    if term.startswith("암"):
        return len(re.findall(r"(?<!상피내)" + re.escape(term), text))
    return text.count(term)


def _audit_query_rows(document, report, issues):
    current_ocr = {}
    for result in document.ocr_results:
        if result.source_sha256 == document.sha256 and result.status == "ocr_ready":
            current_ocr[result.page_number] = result
    if len(current_ocr) != len(document.pages):
        issues.append("조회형 문서의 모든 페이지에 현재 원본과 연결된 OCR이 필요합니다.")
        return
    if (any(any(not str(warning).startswith("정보:") for warning in run.warnings)
            or run.row_count != len(document.pdf_coverages) for run in document.pdf_runs)
            or not document.pdf_runs):
        issues.append("추출 경고 또는 조회형 담보 개수 불일치가 있습니다.")

    rows_by_page = {}
    for row in document.pdf_coverages:
        rows_by_page.setdefault(row.page_number, []).append(row)
    record_types = {row.extracted_fields.get("source_record_type") for row in document.pdf_coverages}
    terms = _QUERY_BENEFIT_NAMES if record_types == {"benefit_detail"} else _QUERY_RIDER_NAMES
    for page in document.pages:
        ocr = current_ocr.get(page.page_number)
        page_text = normalized(" ".join(str(word[4]) for word in (ocr.words if ocr else [])))
        source_count = sum(_query_term_count(page_text, term) for term in terms)
        if source_count:
            report["source_counts"][str(page.page_number)] = source_count
        if source_count != len(rows_by_page.get(page.page_number, [])):
            issues.append(f"{page.page_number}쪽: 조회 원문의 항목과 저장된 항목 개수가 다릅니다.")

    for row in document.pdf_coverages:
        errors = []
        fields = row.extracted_fields
        page = next((item for item in document.pages if item.page_number == row.page_number), None)
        ocr = current_ocr.get(row.page_number)
        box = row.bbox
        if (page is None or ocr is None or not isinstance(box, list) or len(box) != 4
                or box[0] < 0 or box[1] < 0 or box[2] > page.width or box[3] > page.height
                or box[0] >= box[2] or box[1] >= box[3]):
            errors.append("조회 원문 위치 누락")
            section_words = []
        else:
            section_words = [word for word in ocr.words if len(word) >= 5 and _inside_query_box(word, box)]
        section_text = " ".join(str(word[4]) for word in section_words)
        if normalized(fields.get("name")) not in normalized(section_text):
            errors.append("항목명이 조회 원문 위치와 일치하지 않습니다.")
        if fields.get("amount_won") not in _query_amount_candidates(section_text):
            errors.append("원 단위 금액의 독립 OCR 검산이 일치하지 않습니다.")
        source_pages = fields.get("source_pages") or [row.page_number]
        source_text = normalized(" ".join(
            str(word[4]) for page_number in source_pages
            for word in current_ocr.get(page_number, object()).words
        )) if all(page_number in current_ocr for page_number in source_pages) else ""
        if fields.get("source_record_type") == "contract_summary":
            if (not fields.get("payment_period") or not fields.get("insurance_period")
                    or normalized(fields["payment_period"]) not in source_text
                    or normalized(fields["insurance_period"]) not in source_text):
                errors.append("납입기간·보험기간의 조회 원문 검산이 일치하지 않습니다.")
        elif fields.get("source_record_type") == "benefit_detail":
            if not normalized(fields.get("description")):
                errors.append("보장내용 원문이 없습니다.")
        else:
            errors.append("지원하지 않는 조회 항목 종류입니다.")
        report["rows"][str(row.id)] = {
            "passed": not errors,
            "issues": errors,
            "user_edited": row.confirmed_fields is not None,
        }
        report["checked"] += int(not errors)
        for key in SEMANTICS:
            if (row.confirmed_fields or fields).get(key, "unknown") == "unknown":
                report["unresolved"][key] = report["unresolved"].get(key, 0) + 1
    if len(report["rows"]) != len(document.pdf_coverages):
        issues.append("조회 항목의 원문 페이지를 찾을 수 없습니다.")


def _ocr_money_candidates(value):
    return [item.replace(".", ",") for item in re.findall(r"\d[\d,.]*\s*원", value or "")]


def _audit_kyobo_image_rows(document, report, issues):
    """Verify page-bound OCR lines without pretending they are PDF table cells."""
    current_ocr = {
        item.page_number: item for item in document.ocr_results
        if item.source_sha256 == document.sha256 and item.status == "ocr_ready"
    }
    rows_by_page = {}
    for row in document.pdf_coverages:
        rows_by_page.setdefault(row.page_number, []).append(row)
    required_pages = set(rows_by_page)
    if not required_pages or not required_pages.issubset(current_ocr):
        issues.append("교보 이미지형 담보 페이지의 현재 원본 OCR이 없습니다.")
    if (len(document.pdf_runs) != 1
            or document.pdf_runs[0].row_count != len(document.pdf_coverages)):
        issues.append("교보 이미지형 담보 개수와 추출 기록이 일치하지 않습니다.")
    positions = [(row.page_number, row.row_number) for row in document.pdf_coverages]
    if len(set(positions)) != len(positions):
        issues.append("중복된 OCR 담보 행 위치가 있습니다.")

    for page_number, rows in rows_by_page.items():
        report["source_counts"][str(page_number)] = len(rows)
    for row in document.pdf_coverages:
        errors = []
        fields = row.extracted_fields
        ocr = current_ocr.get(row.page_number)
        source_lines = fields.get("ocr_source_lines") or []
        description = fields.get("description") or ""
        if not ocr or not source_lines:
            errors.append("OCR 원문 행 연결 누락")
        else:
            page_text = normalized(ocr.text)
            if any(normalized(line) not in page_text for line in source_lines if normalized(line)):
                errors.append("저장된 OCR 원문 행이 현재 페이지와 일치하지 않습니다.")
            if normalized(description) != normalized(" ".join(source_lines)):
                errors.append("보장내용과 OCR 원문 행이 일치하지 않습니다.")
        candidates = _ocr_money_candidates(description)
        if candidates != (fields.get("ocr_amount_candidates") or []):
            errors.append("OCR 금액 후보가 원문과 일치하지 않습니다.")
        if len(candidates) == 1:
            amount = independent_won(candidates[0])
            if amount is None or amount != fields.get("amount_won"):
                errors.append("단일 OCR 금액의 원 단위 검산이 일치하지 않습니다.")
        elif fields.get("amount_won") is not None:
            errors.append("복수 OCR 금액을 단일 가입금액으로 확정할 수 없습니다.")
        if not normalized(fields.get("name")):
            errors.append("담보명이 없습니다.")
        report["rows"][str(row.id)] = {
            "passed": not errors,
            "issues": errors,
            "user_edited": row.confirmed_fields is not None,
        }
        report["checked"] += int(not errors)
        for key in SEMANTICS:
            if (row.confirmed_fields or fields).get(key, "unknown") == "unknown":
                report["unresolved"][key] = report["unresolved"].get(key, 0) + 1


def audit_document(session, document):
    session.flush()
    fingerprint, digest, content = snapshot(document)
    for previous in reversed(document.pdf_audits):
        if previous.version == VERSION and previous.input_fingerprint == fingerprint:
            return previous.report
    report = dict(summary_ready=False, analysis_ready=False, checked=0,
                  issues=[], rows={}, source_counts={}, unresolved={}, image_pages=[],
                  scope='인식된 4열·6열 담보 표의 전사와 금액·기간 검산. 문서 전체의 완전성·지급 가능성 검증은 아님.')
    issues = report['issues']
    if content is None or digest != document.sha256:
        issues.append('보관 원본이 없거나 변경되어 검산을 중단했습니다.')
    elif not document.pdf_coverages:
        issues.append('검산할 담보 표가 없습니다. 다른 형식 또는 스캔 여부를 확인해야 합니다.')
    elif (len(document.pdf_runs) == 1
          and document.pdf_runs[0].version == KYOBO_IMAGE_VERSION):
        report["scope"] = (
            "교보 이미지형 증권의 페이지별 OCR 원문 행·담보명·금액 후보 연결을 재검산합니다. "
            "PDF 표 칸 좌표나 보험금 지급 가능성을 확인하는 검산은 아닙니다."
        )
        _audit_kyobo_image_rows(document, report, issues)
    elif all(row.extracted_fields.get("source_layout") == QUERY_LAYOUT
             for row in document.pdf_coverages):
        try:
            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                if pdf.page_count != len(document.pages):
                    issues.append("원본과 저장된 페이지 수가 다릅니다.")
            report["scope"] = (
                "교보생명 조회형 화면의 항목명·금액·기간을 좌표 OCR로 재검산합니다. "
                "보험금 지급 가능성이나 약관상 조건 확정은 아닙니다."
            )
            _audit_query_rows(document, report, issues)
        except Exception:
            issues.append("조회형 원문의 독립 검산을 끝내지 못했습니다.")
    elif any(row.extracted_fields.get("source_layout") == QUERY_LAYOUT
             for row in document.pdf_coverages):
        issues.append("서로 다른 추출 형식의 담보가 한 문서에 섞여 있어 검산을 중단했습니다.")
    else:
        try:
            with pdfplumber.open(io.BytesIO(content)) as pdf:
                if len(pdf.pages) != len(document.pages):
                    issues.append('원본과 저장된 페이지 수가 다릅니다.')
                if (any(any(not str(warning).startswith('정보:') for warning in run.warnings)
                        or run.row_count != len(document.pdf_coverages) for run in document.pdf_runs)
                        or not document.pdf_runs):
                    issues.append('추출 경고 또는 담보 개수 불일치가 있습니다.')
                positions = [(r.page_number, r.row_number) for r in document.pdf_coverages]
                if len(set(positions)) != len(positions):
                    issues.append('중복된 담보 행 위치가 있습니다.')
                for page in pdf.pages:
                    rows = [r for r in document.pdf_coverages if r.page_number == page.page_number]
                    source_count = independent_source_row_count(page, rows)
                    if source_count is not None:
                        report['source_counts'][str(page.page_number)] = source_count
                        if source_count != len(rows):
                            issues.append(f'{page.page_number}쪽: 원문 담보 행과 저장된 담보 개수가 다릅니다.')
                    if rows and (source_count is None or page.rotation):
                        issues.append(f'{page.page_number}쪽: 지원하는 표 머리글·방향을 확인할 수 없습니다.')
                    for row in rows:
                        errors = []
                        cell_count = len(row.raw_cells)
                        required_boxes = range(4) if cell_count == 4 else (1, 2, 3, 4, 5)
                        if (cell_count not in {4, 6} or len(row.cell_boxes) != cell_count
                                or not all(row.cell_boxes[index] for index in required_boxes)):
                            errors.append('원문 칸 위치 누락')
                        else:
                            cells = [page.crop(tuple(box)).extract_text(x_tolerance=2, y_tolerance=2) or ''
                                     if box else '' for box in row.cell_boxes]
                            required = range(4) if cell_count == 4 else (1, 2, 3, 4, 5)
                            if (any(normalized(a) != normalized(b) for a, b in zip(cells, row.raw_cells))
                                    or any(not normalized(cells[index]) for index in required)):
                                errors.append('서로 다른 읽기 방식의 결과 불일치')
                            fields = row.extracted_fields
                            if cell_count == 4:
                                for key, index in [('name',0), ('amount_text',1), ('raw_period',2), ('description',3)]:
                                    if normalized(fields.get(key)) != normalized(cells[index]):
                                        errors.append(f'{key}: 저장값과 원문 불일치')
                                amount_text = cells[1]
                            else:
                                if normalized(fields.get('name')) + normalized(fields.get('raw_period')) != normalized(cells[1]):
                                    errors.append('name/raw_period: 저장값과 원문 불일치')
                                if normalized(fields.get('description')) != normalized(cells[5]):
                                    errors.append('description: 저장값과 원문 불일치')
                                if normalized(fields.get('insurance_start')) != normalized(cells[2]) or normalized(fields.get('insurance_end')) != normalized(cells[3]):
                                    errors.append('보험시기·보험종기 저장값과 원문 불일치')
                                amount_text = normalized(cells[4]) + ('만원' if normalized(cells[4]) else '')
                                if normalized(fields.get('amount_text')) != normalized(amount_text):
                                    errors.append('amount_text: 저장값과 원문 불일치')
                            amount = independent_won(amount_text)
                            if amount is None or amount != fields.get('amount_won'):
                                errors.append('원 단위 금액 검산 불일치')
                            period = normalized(cells[2] if cell_count == 4 else fields.get('raw_period'))
                            payment = normalized(fields.get('payment_period'))
                            insurance = normalized(fields.get('insurance_period'))
                            if not re.fullmatch(r'(\d+년납|전기납|일시납)', payment) or not insurance or payment + insurance != period:
                                errors.append('납입기간·보험기간 분리 검산 불일치')
                        report['rows'][str(row.id)] = dict(passed=not errors, issues=errors,
                            user_edited=row.confirmed_fields is not None)
                        report['checked'] += int(not errors)
                        for key in SEMANTICS:
                            if (row.confirmed_fields or row.extracted_fields).get(key, 'unknown') == 'unknown':
                                report['unresolved'][key] = report['unresolved'].get(key, 0) + 1
                if len(report['rows']) != len(document.pdf_coverages):
                    issues.append('담보의 원문 페이지를 찾을 수 없습니다.')
        except Exception:
            # Unrecognized or corrupt geometry must fail closed; do not expose source data.
            issues.append('독립 검산을 끝내지 못했습니다. 원문 형식 또는 칸 위치 확인이 필요합니다.')
    report['image_pages'] = [p.page_number for p in document.pages if p.status != 'text_ready']
    report['summary_ready'] = bool(document.pdf_coverages) and not issues and report['checked'] == len(document.pdf_coverages)
    # Shared conditions, compound benefits and non-table images need a separate interpretation stage.
    # Even all manual enum choices cannot bypass this gate.
    report['analysis_ready'] = False
    document.pdf_audits.append(PdfAudit(version=VERSION, input_fingerprint=fingerprint, report=report))
    session.flush()
    return report
