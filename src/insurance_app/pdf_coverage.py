"""Conservative supported-layout policy table reader; values remain unconfirmed."""
from dataclasses import dataclass, field
from pathlib import Path
import re

import pymupdf

from insurance_app.extraction import parse_korean_won

HEADERS = ['담보명', '가입금액', '납기및만기', '보장내용']
SIX_COLUMN_HEADERS = ['담보명', '', '보험시기', '보험종기', '가입금액(만원)', '보장내용상세(지급조건)']
VERSION = 'supported-policy-tables-v4'


def compact(value):
    return re.sub(r'\s+', '', value or '')


def fields_from_cells(cells):
    name, amount, period, description = (compact(c) for c in cells)
    payment = re.match(r'(\d+년납|전기납|일시납)', period)
    insurance = period[payment.end():] if payment else period
    renewal = 'nonrenewable' if '비갱신' in period + name else ('renewable' if '갱신' in period + name else 'unknown')
    actual = (any(word in name + description for word in
                  ('실손', '손해보상', '손해를보상', '손실보상', '실제부담', '공제금액차감후'))
              or ('본인부담' in description and '보상' in description))
    fixed_amount = bool(re.search(
        r'가입금액(?![^지급]{0,50}한도)[^지급]{0,50}지급(?!하지)',
        description,
    ))
    fixed_table_payout = (
        '지급' in description
        and '보상' not in description
        and '한도' not in description
        and '배상책임' not in name
    )
    compensation = ('actual_cost' if actual else
                    'fixed' if fixed_amount or fixed_table_payout else 'unknown')
    frequency = ('once' if '최초1회' in description or '1회에한' in description else
                 'per_day' if any(word in description for word in ('1일당', '입원일당', '매일')) else
                 'per_event' if any(word in description for word in ('1회당', '건당', '1사고당')) else
                 'repeat' if any(word in description for word in ('매년', '연간', '10년간')) else 'unknown')
    contract_role = ('main' if name == '기본계약' else
                     'rider' if '특약' in name or ('추가' in name and name.endswith('담보')) else
                     'unknown')
    return dict(name=name, amount_text=amount, amount_won=parse_korean_won(amount),
                raw_period=period, insurance_period=insurance or '',
                payment_period=payment.group() if payment else '', renewal=renewal,
                contract_role=contract_role,
                compensation_type=compensation, payment_frequency=frequency,
                description='\n'.join((cells[3] or '').splitlines()))


def fields_from_six_cells(cells, contract_section=''):
    """Map a six-column benefit overview without treating dates as payment terms."""
    detail = compact(cells[1])
    period_match = re.search(r'(?:\d+년납|전기납|일시납).+$', detail)
    raw_period = period_match.group() if period_match else ''
    name = detail[:period_match.start()] if period_match else detail
    amount = compact(cells[4])
    amount_text = amount if not amount or amount.endswith('원') else amount + '만원'
    fields = fields_from_cells([name, amount_text, raw_period, cells[5]])
    section = compact(contract_section or cells[0])
    fields['contract_role'] = ('main' if section == '기본계약' else
                               'rider' if section == '선택계약' else fields['contract_role'])
    fields['insurance_start'] = compact(cells[2])
    fields['insurance_end'] = compact(cells[3])
    fields['source_contract_section'] = section
    return fields


@dataclass
class PdfCoverageResult:
    product: str | None = None
    rows: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _header_lines(page):
    result = {}
    for block in page.get_text('dict')['blocks']:
        for line in block.get('lines', []):
            text = compact(''.join(span['text'] for span in line['spans']))
            if text in HEADERS:
                result[text] = line['bbox']
    return result


def extract_pdf_coverages(path: Path) -> PdfCoverageResult:
    result = PdfCoverageResult()
    previous = None
    contract_section = ''
    with pymupdf.open(stream=path.read_bytes(), filetype='pdf') as pdf:
        for page in pdf:
            for line in page.get_text().splitlines():
                if re.fullmatch(r'.+보험\(Hi\d+\).+', compact(line)):
                    result.product = compact(line)
                    break
            tables = page.find_tables(strategy='lines_strict').tables
            six_column = [t for t in tables if t.col_count == 6 and
                          [compact(c) for c in t.extract()[0]] == SIX_COLUMN_HEADERS]
            if len(six_column) > 1:
                result.warnings.append(f'{page.number + 1}쪽: 6열 담보 표가 여러 개여서 확인이 필요합니다.')
                continue
            if six_column:
                table = six_column[0]
                for index, (cells, row) in enumerate(zip(table.extract()[1:], table.rows[1:]), 1):
                    if compact(cells[0]):
                        contract_section = compact(cells[0])
                    if not any(compact(c) for c in cells):
                        result.warnings.append(f'{page.number + 1}쪽 {index}행: 빈 담보 행 확인이 필요합니다.')
                        continue
                    fields = fields_from_six_cells(cells, contract_section)
                    if not fields['amount_text'] and not compact(cells[5]):
                        result.warnings.append(
                            f'정보: {page.number + 1}쪽 {index}행의 가입금액·보장내용이 없는 계약 항목은 담보 후보에서 제외했습니다.')
                        continue
                    result.rows.append(dict(page_number=page.number + 1, row_number=index,
                        fields=fields, cells=cells,
                        boxes=[list(b) if b else None for b in row.cells], bbox=list(row.bbox)))
                continue

            headers = _header_lines(page)
            if len(headers) != 4:
                continue
            if max(box[1] for box in headers.values()) - min(box[1] for box in headers.values()) > 3:
                result.warnings.append(f'{page.number + 1}쪽: 표 제목 정렬이 달라 자동 추출하지 않았습니다.')
                continue
            matched = [t for t in tables if t.col_count == 4 and
                       [compact(c) for c in t.extract()[0]] == HEADERS]
            # Some continuation pages omit outer borders. Reuse only a previously
            # observed grid with the same page width and matching header x positions.
            if not matched and previous:
                xs, old_headers, width = previous
                if abs(width - page.rect.width) < 2 and all(abs(headers[h][0] - old_headers[h][0]) < 2 for h in HEADERS):
                    end = page.rect.height
                    for block in page.get_text('dict')['blocks']:
                        for line in block.get('lines', []):
                            text = compact(''.join(s['text'] for s in line['spans']))
                            if '추가정보사항' in text and line['bbox'][1] > headers['담보명'][3]:
                                end = min(end, line['bbox'][1])
                    tables = page.find_tables(strategy='lines_strict', vertical_lines=xs,
                        clip=pymupdf.Rect(xs[0], min(b[1] for b in headers.values()) - 12,
                                          xs[-1], end)).tables
                    matched = [t for t in tables if t.col_count == 4 and
                               [compact(c) for c in t.extract()[0]] == HEADERS]
            if len(matched) != 1:
                result.warnings.append(f'{page.number + 1}쪽: 담보 표를 하나로 식별하지 못했습니다.')
                continue
            table = matched[0]
            boxes = table.rows[0].cells
            previous = ([b[0] for b in boxes] + [boxes[-1][2]], headers, page.rect.width)
            for index, (cells, row) in enumerate(zip(table.extract()[1:], table.rows[1:]), 1):
                if not any(compact(c) for c in cells):
                    result.warnings.append(f'{page.number + 1}쪽 {index}행: 빈 담보 행 확인이 필요합니다.')
                    continue
                result.rows.append(dict(page_number=page.number + 1, row_number=index,
                    fields=fields_from_cells(cells), cells=cells,
                    boxes=[list(b) if b else None for b in row.cells], bbox=list(row.bbox)))
    return result
