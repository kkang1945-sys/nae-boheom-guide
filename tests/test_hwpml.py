from pathlib import Path

import pytest

from insurance_app.hwpml import HwpmlError, read_hwpml


def test_reads_paragraph_text_without_changing_source(tmp_path: Path) -> None:
    source = tmp_path / "sample.hwp"
    content = b'<?xml version="1.0" encoding="utf-8"?><HWPML><BODY><P><TEXT>A</TEXT><TEXT>B</TEXT></P></BODY></HWPML>'
    source.write_bytes(content)

    document = read_hwpml(source)

    assert source.read_bytes() == content
    assert document.source_name == "sample.hwp"
    assert document.text == "A B"
    assert document.table_rows == ()


def test_reads_char_nodes_used_by_real_hwpml(tmp_path: Path) -> None:
    source = tmp_path / "sample.hwp"
    source.write_text(
        '<HWPML><BODY><P><TEXT><CHAR>보험회사</CHAR><CHAR>상품명</CHAR></TEXT></P></BODY></HWPML>',
        encoding="utf-8",
    )

    document = read_hwpml(source)

    assert document.text == "보험회사 상품명"


def test_preserves_table_row_and_cell_order(tmp_path: Path) -> None:
    source = tmp_path / "sample.hwp"
    source.write_text(
        """<HWPML><BODY><TABLE><ROW>
        <CELL><P><TEXT><CHAR>담보명</CHAR></TEXT></P></CELL>
        <CELL><P><TEXT><CHAR>가입금액</CHAR></TEXT></P></CELL>
        </ROW></TABLE></BODY></HWPML>""",
        encoding="utf-8",
    )

    document = read_hwpml(source)

    assert len(document.table_rows) == 1
    assert document.table_rows[0].cells == ("담보명", "가입금액")


def test_rejects_non_hwpml_xml(tmp_path: Path) -> None:
    source = tmp_path / "sample.xml"
    source.write_text("<document><P><TEXT>not hwpml</TEXT></P></document>", encoding="utf-8")

    with pytest.raises(HwpmlError, match="HWPML 문서가 아닙니다"):
        read_hwpml(source)


def test_rejects_oversized_file(tmp_path: Path) -> None:
    source = tmp_path / "sample.hwp"
    source.write_text("<HWPML />", encoding="utf-8")

    with pytest.raises(HwpmlError, match="크기"):
        read_hwpml(source, max_bytes=2)
