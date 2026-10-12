"""Local PDF evidence extraction. Reading success is not coverage verification."""
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from insurance_app.privacy_masking import sensitive_values, sensitive_word_boxes


class PdfReadError(ValueError):
    pass


@dataclass(frozen=True)
class PdfPageEvidence:
    page_number: int
    width: float
    height: float
    rotation: int
    text: str
    words: list
    status: str


def page_status(text: str, has_images: bool) -> str:
    meaningful = ''.join(text.split())
    if not meaningful:
        return 'needs_ocr'
    if '\ufffd' in meaningful or not any(c.isalnum() for c in meaningful):
        return 'needs_ocr'
    # A text header can coexist with a scanned body. Never call that complete.
    if has_images:
        return 'mixed_review'
    return 'text_ready'


def read_pdf(path: Path) -> list[PdfPageEvidence]:
    try:
        # A failed native open can retain a Windows file handle in its traceback.
        # Opening bytes keeps cleanup reliable even when the PDF is malformed.
        with pymupdf.open(stream=path.read_bytes(), filetype='pdf') as pdf:
            if not pdf.is_pdf:
                raise PdfReadError('실제 PDF 파일이 아닙니다.')
            if pdf.needs_pass:
                raise PdfReadError('암호로 잠긴 PDF입니다. 이 PC에서 잠금을 해제한 사본을 추가해 주세요.')
            if not pdf.page_count:
                raise PdfReadError('페이지가 없는 PDF입니다.')
            pages = []
            for page in pdf:
                text = page.get_text('text', sort=True)
                words = [list(word) for word in page.get_text('words', sort=True)]
                pages.append(PdfPageEvidence(
                    page.number + 1, page.cropbox.width, page.cropbox.height,
                    page.rotation, text, words,
                    page_status(text, bool(page.get_image_info())),
                ))
            return pages
    except PdfReadError:
        raise
    except (RuntimeError, ValueError, OSError) as exc:
        raise PdfReadError('PDF를 읽을 수 없습니다. 파일 손상 여부를 확인해 주세요.') from exc


def render_page(path: Path, page_number: int, highlight: list | None = None,
                extra_redaction_boxes: list | None = None,
                extra_redaction_values: list[str] | None = None) -> bytes:
    """Render a masked in-memory preview; the stored original is never edited."""
    try:
        with pymupdf.open(stream=path.read_bytes(), filetype='pdf') as pdf:
            if pdf.needs_pass or not 1 <= page_number <= pdf.page_count:
                raise PdfReadError('원문 페이지를 열 수 없습니다.')
            page = pdf[page_number - 1]
            boxes = sensitive_word_boxes([list(word) for word in page.get_text('words', sort=True)])
            for value in sensitive_values(page.get_text('text', sort=True)):
                boxes.extend([list(rect) for rect in page.search_for(value)])
            for value in extra_redaction_values or []:
                boxes.extend([list(rect) for rect in page.search_for(value)])
            boxes.extend(extra_redaction_boxes or [])
            seen = set()
            for box in boxes:
                key = tuple(round(float(value), 2) for value in box[:4])
                if key in seen:
                    continue
                seen.add(key)
                page.add_redact_annot(pymupdf.Rect(box[:4]), fill=(0, 0, 0), text="")
            if seen:
                page.apply_redactions()
            if highlight:
                page.draw_rect(pymupdf.Rect(highlight), color=(1, 0.35, 0), width=2, overlay=True)
            scale = min(1.5, 1800 / max(page.rect.width, page.rect.height))
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes('png')
    except (RuntimeError, ValueError, OSError) as exc:
        raise PdfReadError('원문 페이지를 열 수 없습니다.') from exc
