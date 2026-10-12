"""HIRA disease statistics used only as public medical-use context."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import urlopen
from xml.etree import ElementTree

BASE_URL = "https://apis.data.go.kr/B551182/diseaseInfoService1"
SOURCE_URL = "https://www.data.go.kr/data/15119055/openapi.do"
VERIFIED_ON = "2026-09-16"
CACHE_VERSION = "hira-disease-cache-v1"
OPERATIONS = {
    "disease_search": "getDissNameCodeList1",
    "gender_age": "getDissByGenderAgeStats1",
    "inpatient_outpatient": "getDissByHsptlzFrgnStats1",
}
CODE_PATTERN = re.compile(r"^[A-Z][0-9A-Z.]{1,9}$")


class HiraDiseaseError(RuntimeError):
    """Safe failure that never includes the service key or request URL."""


def _text(element, path: str) -> str | None:
    found = element.find(path)
    return found.text.strip() if found is not None and found.text else None


def parse_hira_xml(payload: bytes) -> dict:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise HiraDiseaseError("심평원 응답 형식을 읽을 수 없습니다.") from exc
    result_code = _text(root, ".//resultCode")
    result_message = _text(root, ".//resultMsg")
    if result_code not in {None, "00", "0"}:
        raise HiraDiseaseError(f"심평원 API 오류({result_code})가 발생했습니다.")
    items = [
        {child.tag.split("}")[-1]: (child.text or "").strip() for child in item}
        for item in root.findall(".//item")
    ]
    return {
        "result_code": result_code, "result_message": result_message,
        "total_count": int(_text(root, ".//totalCount") or len(items)),
        "page_no": int(_text(root, ".//pageNo") or 1),
        "num_of_rows": int(_text(root, ".//numOfRows") or len(items)),
        "items": items,
    }


class HiraDiseaseClient:
    def __init__(self, service_key: str, cache_dir: Path, *, timeout: float = 10,
                 cache_hours: int = 168, opener=urlopen):
        key = unquote((service_key or "").strip())
        if not key:
            raise ValueError("심평원 API 키가 설정되지 않았습니다.")
        self._service_key = key
        self.cache_dir = Path(cache_dir)
        self.timeout = timeout
        self.cache_age = timedelta(hours=cache_hours)
        self._opener = opener

    def _cache_path(self, operation: str, params: dict[str, str | int]) -> Path:
        public_input = json.dumps({"version": CACHE_VERSION, "operation": operation,
                                   "params": sorted(params.items())}, ensure_ascii=False)
        digest = hashlib.sha256(public_input.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def fetch(self, operation: str, params: dict[str, str | int], *, refresh: bool = False) -> dict:
        if operation not in OPERATIONS:
            raise ValueError("지원하지 않는 심평원 조회입니다.")
        cache_path = self._cache_path(operation, params)
        if not refresh and cache_path.is_file():
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(cached["fetched_at"])
            if datetime.now(timezone.utc) - fetched_at <= self.cache_age:
                return cached | {"cache_hit": True}
        query = {"ServiceKey": self._service_key, "pageNo": 1, "numOfRows": 100,
                 "_type": "xml"} | params
        url = f"{BASE_URL}/{OPERATIONS[operation]}?{urlencode(query)}"
        try:
            with self._opener(url, timeout=self.timeout) as response:
                parsed = parse_hira_xml(response.read())
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise HiraDiseaseError("심평원 통계에 연결하지 못했습니다. 잠시 후 다시 확인해 주세요.") from exc
        result = parsed | {
            "operation": operation, "parameters": params,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source_url": SOURCE_URL, "verified_on": VERIFIED_ON,
            "cache_hit": False,
            "scope": ("건강보험 청구 기반 의료이용 통계이며 개인의 치료기간·휴직기간·보험금 "
                      "필요액을 의미하지 않습니다."),
        }
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result

    def _statistics(self, operation: str, sick_code: str, year: int) -> dict:
        code = sick_code.strip().upper()
        if not CODE_PATTERN.fullmatch(code):
            raise ValueError("질병코드를 영문과 숫자로 입력해 주세요. 예: C50, I63")
        if not 2000 <= int(year) <= datetime.now().year:
            raise ValueError("조회 연도를 확인해 주세요.")
        return self.fetch(operation, {
            "sickCd": code, "sickType": "1", "medTp": "1", "year": int(year),
        })

    def inpatient_outpatient(self, sick_code: str, year: int) -> dict:
        return self._statistics("inpatient_outpatient", sick_code, year)

    def gender_age(self, sick_code: str, year: int) -> dict:
        return self._statistics("gender_age", sick_code, year)
