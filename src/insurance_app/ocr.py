"""Local-only page OCR with source-bound caching."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import pymupdf
from sqlalchemy.orm import Session

from insurance_app.models import OcrPageResult

LANGUAGES = "kor+eng"
DPI = 300
PAGE_SEGMENTATION_MODE = 4
VERSION = "local-tesseract-tsv-v3"


class OcrError(ValueError):
    pass


def engine_version(executable: Path) -> str:
    try:
        completed = subprocess.run([str(executable), "--version"], capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", timeout=10,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        raise OcrError("로컬 OCR 실행기를 시작할 수 없습니다.") from exc
    if completed.returncode:
        raise OcrError("로컬 OCR 실행기 버전을 확인할 수 없습니다.")
    return completed.stdout.splitlines()[0].strip()[:120]


def _parse_tsv(value: str, page_width: float, page_height: float, image_width: int, image_height: int):
    words, lines = [], {}
    records = value.splitlines()
    if not records:
        return "", []
    headers = records[0].split("\t")
    for record in records[1:]:
        parts = record.split("\t", len(headers) - 1)
        if len(parts) != len(headers):
            continue
        row = dict(zip(headers, parts, strict=True))
        text = (row.get("text") or "").strip()
        try:
            confidence = float(row.get("conf", "-1"))
            left, top, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
        except (ValueError, KeyError):
            continue
        if (not text or confidence < 0 or len(text) > 200
                or any(character in text for character in ("\t", "\r", "\n"))):
            continue
        box = [left * page_width / image_width, top * page_height / image_height,
               (left + width) * page_width / image_width, (top + height) * page_height / image_height]
        words.append([*box, text, confidence])
        key = (row.get("block_num"), row.get("par_num"), row.get("line_num"))
        lines.setdefault(key, []).append(text)
    return "\n".join(" ".join(parts) for parts in lines.values()), words


def recognize_page(path: Path, page_number: int, executable: Path, tessdata_dir: Path,
                   *, dpi: int = DPI) -> dict:
    if not executable.is_file() or not (tessdata_dir / "kor.traineddata").is_file():
        raise OcrError("한국어 로컬 OCR 실행기와 학습 데이터가 필요합니다.")
    version = engine_version(executable)
    try:
        with pymupdf.open(stream=path.read_bytes(), filetype="pdf") as pdf:
            if pdf.needs_pass or not 1 <= page_number <= pdf.page_count:
                raise OcrError("OCR할 원문 페이지를 열 수 없습니다.")
            page = pdf[page_number - 1]
            scale = dpi / 72
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            with tempfile.TemporaryDirectory(prefix="insurance-ocr-") as directory:
                image_path = Path(directory) / "page.png"
                pixmap.save(image_path)
                command = [str(executable), str(image_path), "stdout", "--tessdata-dir", str(tessdata_dir),
                           "-l", LANGUAGES, "--psm", str(PAGE_SEGMENTATION_MODE),
                           "-c", "tessedit_create_tsv=1"]
                completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                           errors="replace", timeout=120,
                                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if completed.returncode:
                raise OcrError("로컬 OCR이 페이지를 읽지 못했습니다.")
            text, words = _parse_tsv(completed.stdout, page.rect.width, page.rect.height,
                                     pixmap.width, pixmap.height)
    except OcrError:
        raise
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        raise OcrError("로컬 OCR 처리 중 오류가 발생했습니다.") from exc
    return {"text": text, "words": words, "status": "ocr_ready" if text.strip() else "ocr_empty",
            "engine_version": version, "languages": LANGUAGES, "dpi": dpi}


def ocr_page(session: Session, document, page_number: int, executable: Path, tessdata_dir: Path,
             *, force: bool = False) -> OcrPageResult:
    source = Path(document.source_path)
    if not source.is_file():
        raise OcrError("보관된 원본 파일이 없습니다.")
    content = source.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if digest != document.sha256:
        raise OcrError("원본 파일이 변경되어 OCR을 실행할 수 없습니다.")
    if not any(page.page_number == page_number for page in document.pages):
        raise OcrError("OCR할 원문 페이지를 찾을 수 없습니다.")
    version = engine_version(executable)
    payload = {"source": digest, "page": page_number, "version": VERSION,
               "engine": version, "languages": LANGUAGES, "dpi": DPI,
               "page_segmentation_mode": PAGE_SEGMENTATION_MODE}
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    cached = next((item for item in reversed(document.ocr_results)
                   if item.page_number == page_number and item.input_fingerprint == fingerprint), None)
    if cached and not force:
        return cached
    result = recognize_page(source, page_number, executable, tessdata_dir)
    record = OcrPageResult(document_id=document.id, page_number=page_number,
        source_sha256=digest, input_fingerprint=fingerprint, engine_version=result["engine_version"],
        languages=result["languages"], dpi=result["dpi"], text=result["text"], words=result["words"],
        status=result["status"], error_message=None)
    document.ocr_results.append(record)
    session.flush()
    return record


def current_ocr(document, page_number: int):
    return next((item for item in reversed(document.ocr_results)
                 if item.page_number == page_number and item.source_sha256 == document.sha256), None)
