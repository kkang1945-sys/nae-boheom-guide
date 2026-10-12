"""Official living-cost reference; never an insurance recommendation."""
from __future__ import annotations

REFERENCE_YEAR = 2026
VERIFIED_ON = "2026-09-15"
SOURCE_TITLE = "보건복지부 2026년 생계급여 선정기준 및 최저보장수준"
SOURCE_URL = "https://www.mohw.go.kr/menu.es?mid=a10708010300"

# 생계급여 선정기준(기준 중위소득 32%). This is not observed household
# spending and must not replace the user's own monthly expenses.
MONTHLY_MINIMUM_BY_HOUSEHOLD_SIZE = {
    1: 820_556,
    2: 1_343_773,
    3: 1_714_892,
    4: 2_078_316,
    5: 2_418_150,
    6: 2_737_905,
    7: 3_044_848,
}


def minimum_living_reference(household_size: int | None) -> dict | None:
    if type(household_size) is not int or household_size < 1:
        return None
    if household_size <= 7:
        amount = MONTHLY_MINIMUM_BY_HOUSEHOLD_SIZE[household_size]
    else:
        amount = MONTHLY_MINIMUM_BY_HOUSEHOLD_SIZE[7] + (household_size - 7) * 306_943
    return {
        "year": REFERENCE_YEAR,
        "household_size": household_size,
        "monthly_won": amount,
        "source_title": SOURCE_TITLE,
        "source_url": SOURCE_URL,
        "verified_on": VERIFIED_ON,
        "scope": (
            "생계급여 선정기준이자 최저보장수준을 기간 비교용 참고선으로만 사용했습니다. "
            "일반 가구의 평균 지출이나 정부의 민간보험 권장금액이 아닙니다."
        ),
    }
