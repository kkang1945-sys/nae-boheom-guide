"""Auditable, read-only analysis of public insurance reference documents."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import pymupdf

from insurance_app.coverage_rules import standardize_coverage_name
from insurance_app.policy_conditions import categories_for_text


CORPUS_REPORT_VERSION = "public-corpus-report-v1"
KEYWORDS = (
    "실손", "암진단", "유사암", "소액암", "뇌혈관", "뇌졸중",
    "허혈성심장", "급성심근경색", "질병수술", "상해수술", "입원",
    "후유장해", "간병", "치매", "질병사망", "상해사망",
)
ALLOWED_SOURCE_TYPES = {"product_summary", "policy_terms", "ocr_dataset"}
ALLOWED_ACCESS = {"public_direct_download", "download_application_required"}


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    return manifest


def validate_manifest(manifest: dict) -> None:
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("sources"), list):
        raise ValueError("지원하지 않는 공개 말뭉치 명세입니다.")
    seen: set[str] = set()
    for source in manifest["sources"]:
        source_id = source.get("id")
        if not source_id or source_id in seen:
            raise ValueError("공개 자료 ID는 비어 있지 않고 서로 달라야 합니다.")
        seen.add(source_id)
        if source.get("source_type") not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"{source_id}: 지원하지 않는 자료 유형입니다.")
        if source.get("access") not in ALLOWED_ACCESS:
            raise ValueError(f"{source_id}: 접근 방식을 명시해야 합니다.")
        for field in ("landing_url",):
            url = source.get(field, "")
            if urlparse(url).scheme != "https" or not urlparse(url).netloc:
                raise ValueError(f"{source_id}: 공식 HTTPS 출처가 필요합니다.")
        download_url = source.get("download_url")
        if download_url and urlparse(download_url).scheme != "https":
            raise ValueError(f"{source_id}: 다운로드 주소는 HTTPS여야 합니다.")
        filename = source.get("local_filename")
        if filename and (Path(filename).name != filename or Path(filename).suffix.lower() != ".pdf"):
            raise ValueError(f"{source_id}: 로컬 PDF 파일명이 올바르지 않습니다.")
        if source.get("access") == "public_direct_download" and not (filename and source.get("sha256")):
            raise ValueError(f"{source_id}: 공개 파일명과 SHA-256이 필요합니다.")


def _clean_line(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("\x01", " ")).strip()


def _is_candidate(value: str) -> bool:
    compact = value.replace(" ", "")
    return 4 <= len(value) <= 160 and any(keyword in compact for keyword in KEYWORDS)


def analyze_pdf(path: Path, expected_sha256: str) -> dict:
    raw = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(f"{path.name}: 공개 원본의 SHA-256이 명세와 다릅니다.")
    keyword_counts: Counter[str] = Counter()
    condition_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    candidate_fingerprints: set[str] = set()
    text_character_count = 0
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ValueError(f"{path.name}: 암호화된 PDF는 기준 자료로 사용하지 않습니다.")
        for page in document:
            page_text = page.get_text()
            text_character_count += len(page_text)
            compact_page = page_text.replace(" ", "").replace("\n", "")
            for keyword in KEYWORDS:
                keyword_counts[keyword] += compact_page.count(keyword)
            for raw_line in page_text.splitlines():
                line = _clean_line(raw_line)
                if not _is_candidate(line):
                    continue
                fingerprint = hashlib.sha256(line.encode("utf-8")).hexdigest()
                if fingerprint in candidate_fingerprints:
                    continue
                candidate_fingerprints.add(fingerprint)
                group = standardize_coverage_name(line) or "분류 확인 필요"
                classification_counts[group] += 1
                condition_counts.update(categories_for_text(line))
        return {
            "status": "analyzed",
            "sha256": actual_sha256,
            "page_count": document.page_count,
            "text_character_count": text_character_count,
            "unique_candidate_line_count": len(candidate_fingerprints),
            "keyword_counts": dict(sorted(keyword_counts.items())),
            "classification_counts": dict(sorted(classification_counts.items())),
            "condition_category_counts": dict(sorted(condition_counts.items())),
        }


def build_report(manifest: dict, corpus_dir: Path) -> dict:
    sources = []
    for source in manifest["sources"]:
        record = {"id": source["id"], "source_type": source["source_type"], "access": source["access"]}
        filename = source.get("local_filename")
        if not filename:
            record["status"] = "manual_download_required"
        else:
            path = corpus_dir / filename
            record.update(analyze_pdf(path, source["sha256"]) if path.is_file() else {"status": "missing"})
        sources.append(record)
    return {"report_version": CORPUS_REPORT_VERSION, "sources": sources}
