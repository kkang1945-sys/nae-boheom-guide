"""보험 문서에서 확인 전 후보만 만드는 규칙 기반 추출기."""

from __future__ import annotations

import re
from dataclasses import dataclass

from insurance_app.hwpml import HwpmlDocument


PRODUCT_PATTERN = re.compile(r"(?P<name>.+보험\(Hi\d+\)[^\s]*)")


@dataclass(frozen=True)
class CoverageCandidate:
    raw_name: str
    raw_amount: str
    amount_won: int | None
    raw_period: str
    renewal: bool
    compensation_type: str
    benefit_description: str
    table_sequence: int
    row_sequence: int
    review_status: str = "needs_review"


@dataclass(frozen=True)
class PolicyCandidates:
    insurer: str | None
    product_name: str | None
    coverages: tuple[CoverageCandidate, ...]


def parse_korean_won(value: str) -> int | None:
    compact = re.sub(r"[\s,]", "", value)
    if not compact.endswith("원"):
        return None
    body = compact[:-1]
    eok = 0
    if "억" in body:
        eok_text, body = body.split("억", 1)
        if not eok_text.isdigit():
            return None
        eok = int(eok_text)

    man = 0
    if body:
        if not body.endswith("만"):
            return None
        man_text = body[:-1]
        match = re.fullmatch(r"(?:(\d+)천)?(?:(\d+)백)?(?:(\d+)십)?(?:(\d+))?", man_text)
        if not match or not any(match.groups()):
            return None
        cheon, baek, sip, ones = (int(part or 0) for part in match.groups())
        man = cheon * 1_000 + baek * 100 + sip * 10 + ones
    if not eok and not man:
        return None
    return eok * 100_000_000 + man * 10_000


def extract_policy_candidates(document: HwpmlDocument) -> PolicyCandidates:
    insurer = (
        "현대해상"
        if "현대해상" in document.source_name or any("현대해상" in block.text for block in document.blocks)
        else None
    )
    product_name = None
    for block in document.blocks:
        match = PRODUCT_PATTERN.search(block.text)
        if match:
            product_name = match.group("name")
            break

    coverages: list[CoverageCandidate] = []
    seen_locations: set[tuple[int, int]] = set()
    for row in document.table_rows:
        if len(row.cells) != 4:
            continue
        name, amount, period, description = (cell.strip() for cell in row.cells)
        amount_won = parse_korean_won(amount)
        if amount_won is None or not name or not description:
            continue
        if "만기" not in period and "갱신" not in period:
            continue
        location = (row.table_sequence, row.row_sequence)
        if location in seen_locations:
            continue
        seen_locations.add(location)
        compensation_type = "actual_cost" if "실손" in name or "실손" in description else "fixed"
        coverages.append(
            CoverageCandidate(
                raw_name=name,
                raw_amount=amount,
                amount_won=amount_won,
                raw_period=period,
                renewal="갱신" in period or "갱신형" in name,
                compensation_type=compensation_type,
                benefit_description=description,
                table_sequence=row.table_sequence,
                row_sequence=row.row_sequence,
            )
        )

    return PolicyCandidates(insurer, product_name, tuple(coverages))
