"""Compare a saved result with a current recalculation without merging risks."""
from __future__ import annotations


def _key(item):
    return item.get("risk_kind", ""), item.get("standard_name", "")


def compare_reports(saved: dict, current: dict) -> dict:
    saved_rows = {_key(item): item for item in saved.get("scenarios", [])}
    current_rows = {_key(item): item for item in current.get("scenarios", [])}
    rows = []
    for key in sorted(saved_rows.keys() | current_rows.keys()):
        before, after = saved_rows.get(key), current_rows.get(key)
        before_gap = before.get("gap_won") if before else None
        after_gap = after.get("gap_won") if after else None
        rows.append({
            "risk_kind": key[0], "standard_name": key[1],
            "saved_status": before.get("status") if before else "not_present",
            "current_status": after.get("status") if after else "not_present",
            "saved_gap_won": before_gap, "current_gap_won": after_gap,
            "gap_change_won": after_gap - before_gap if isinstance(before_gap, int) and isinstance(after_gap, int) else None,
        })
    return {"rows": rows, "scope": "저장 당시 결과와 현재 입력으로 다시 계산한 결과를 위험별로 비교합니다. 서로 다른 위험군의 금액은 합산하지 않습니다."}
