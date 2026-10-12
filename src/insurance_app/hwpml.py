"""HWPML 문서를 원본 변경 없이 읽는 최소 입력 어댑터."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree


class HwpmlError(ValueError):
    """안전하게 읽을 수 없는 HWPML 문서."""


@dataclass(frozen=True)
class TextBlock:
    sequence: int
    kind: str
    text: str


@dataclass(frozen=True)
class TableRow:
    table_sequence: int
    row_sequence: int
    cells: tuple[str, ...]


@dataclass(frozen=True)
class HwpmlDocument:
    source_name: str
    blocks: tuple[TextBlock, ...]
    table_rows: tuple[TableRow, ...]

    @property
    def text(self) -> str:
        return "\n".join(block.text for block in self.blocks)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].upper()


def _element_text(element: ElementTree.Element) -> str:
    char_parts = [
        (node.text or "").strip()
        for node in element.iter()
        if _local_name(node.tag) == "CHAR" and (node.text or "").strip()
    ]
    parts = char_parts or [
        (node.text or "").strip()
        for node in element.iter()
        if _local_name(node.tag) == "TEXT" and (node.text or "").strip()
    ]
    return " ".join(parts).strip()


def read_hwpml(path: Path, *, max_bytes: int = 25 * 1024 * 1024) -> HwpmlDocument:
    """HWPML의 문단 텍스트를 읽는다.

    외부 엔티티나 DTD를 허용하지 않는 Python 기본 XML 파서를 사용하고,
    파일 크기와 루트 요소를 먼저 검증한다. 원본 파일은 쓰지 않는다.
    """

    source = path.resolve(strict=True)
    if source.stat().st_size > max_bytes:
        raise HwpmlError("허용된 문서 크기를 초과했습니다.")
    if source.suffix.lower() not in {".hwp", ".xml"}:
        raise HwpmlError("HWPML로 허용된 확장자가 아닙니다.")

    try:
        root = ElementTree.parse(source).getroot()
    except ElementTree.ParseError as exc:
        raise HwpmlError("손상되었거나 지원하지 않는 HWPML 문서입니다.") from exc

    if _local_name(root.tag) != "HWPML":
        raise HwpmlError("HWPML 문서가 아닙니다.")

    blocks: list[TextBlock] = []
    for element in root.iter():
        if _local_name(element.tag) == "P":
            text = _element_text(element)
            if text:
                blocks.append(TextBlock(len(blocks) + 1, "paragraph", text))

    table_rows: list[TableRow] = []
    tables = [element for element in root.iter() if _local_name(element.tag) == "TABLE"]
    for table_number, table in enumerate(tables, start=1):
        rows = [element for element in table if _local_name(element.tag) == "ROW"]
        for row_number, row in enumerate(rows, start=1):
            cells = tuple(
                _element_text(cell)
                for cell in row
                if _local_name(cell.tag) == "CELL"
            )
            if any(cells):
                table_rows.append(TableRow(table_number, row_number, cells))

    return HwpmlDocument(source.name, tuple(blocks), tuple(table_rows))
