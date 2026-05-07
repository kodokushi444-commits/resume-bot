from __future__ import annotations

from html import escape
from io import BytesIO
from typing import Iterable
from zipfile import ZIP_DEFLATED, ZipFile


def build_xlsx_workbook(sheets: Iterable[dict]) -> bytes:
    normalized_sheets = []
    used_names: set[str] = set()
    for item in sheets:
        rows = [list(row) for row in item.get("rows", [])]
        if not rows:
            continue
        normalized_sheets.append(
            {
                "name": _sanitize_sheet_name(str(item.get("name", "Sheet")), used_names),
                "rows": rows,
                "column_widths": list(item.get("column_widths", []) or []),
            }
        )
    if not normalized_sheets:
        raise ValueError("至少要有一个非空 sheet。")

    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml(len(normalized_sheets)))
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml(normalized_sheets))
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(len(normalized_sheets)))
        archive.writestr("xl/styles.xml", _styles_xml())
        for index, sheet in enumerate(normalized_sheets, start=1):
            archive.writestr(
                f"xl/worksheets/sheet{index}.xml",
                _worksheet_xml(sheet["rows"], sheet["column_widths"]),
            )
    return buffer.getvalue()


def _sanitize_sheet_name(name: str, used_names: set[str]) -> str:
    cleaned = "".join(" " if char in '[]:*?/\\\\' else char for char in name).strip() or "Sheet"
    cleaned = cleaned[:31]
    candidate = cleaned
    suffix = 2
    while candidate in used_names:
        tag = f"_{suffix}"
        candidate = f"{cleaned[: 31 - len(tag)]}{tag}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _content_types_xml(sheet_count: int) -> str:
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
        f'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{sheet_overrides}"
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheets: list[dict]) -> str:
    sheet_entries = "".join(
        f'<sheet name="{escape(sheet["name"])}" sheetId="{index}" r:id="rId{index}"/>'
        for index, sheet in enumerate(sheets, start=1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        f"{sheet_entries}"
        "</sheets>"
        "</workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    sheet_rels = "".join(
        f'<Relationship Id="rId{index}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    )
    style_id = sheet_count + 1
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{sheet_rels}"
        f'<Relationship Id="rId{style_id}" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" '
        'Target="styles.xml"/>'
        "</Relationships>"
    )


def _styles_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font>'
        "</fonts>"
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF0F766E"/><bgColor indexed="64"/></patternFill></fill>'
        "</fills>"
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"/><right style="thin"/><top style="thin"/><bottom style="thin"/><diagonal/></border>'
        "</borders>"
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
        '<alignment horizontal="center" vertical="center" wrapText="1"/>'
        "</xf>"
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1">'
        '<alignment vertical="top" wrapText="1"/>'
        "</xf>"
        "</cellXfs>"
        "</styleSheet>"
    )


def _worksheet_xml(rows: list[list[object]], configured_widths: list[float] | None = None) -> str:
    column_widths = _column_widths(rows, configured_widths or [])
    columns_xml = "".join(
        f'<col min="{index}" max="{index}" width="{width:.2f}" customWidth="1"/>'
        for index, width in enumerate(column_widths, start=1)
    )
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        style_id = 1 if row_index == 1 else 2
        for column_index, value in enumerate(row, start=1):
            ref = f"{_column_name(column_index)}{row_index}"
            cells.append(_cell_xml(ref, value, style_id))
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    last_col = _column_name(max(len(rows[0]), 1))
    auto_filter_ref = f"A1:{last_col}1"
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/>'
        '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="18"/>'
        f"<cols>{columns_xml}</cols>"
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        f'<autoFilter ref="{auto_filter_ref}"/>'
        '<pageMargins left="0.5" right="0.5" top="0.75" bottom="0.75" header="0.3" footer="0.3"/>'
        "</worksheet>"
    )


def _cell_xml(ref: str, value: object, style_id: int) -> str:
    if value is None:
        value = ""
    if isinstance(value, bool):
        value = "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}" s="{style_id}"><v>{value}</v></c>'
    text = str(value)
    text_xml = escape(text)
    if text != text.strip() or "\n" in text:
        text_node = f'<t xml:space="preserve">{text_xml}</t>'
    else:
        text_node = f"<t>{text_xml}</t>"
    return f'<c r="{ref}" s="{style_id}" t="inlineStr"><is>{text_node}</is></c>'


def _column_name(index: int) -> str:
    result = []
    value = index
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        result.append(chr(65 + remainder))
    return "".join(reversed(result))


def _column_widths(rows: list[list[object]], configured_widths: list[float]) -> list[float]:
    column_count = max(len(row) for row in rows)
    widths = [12.0] * column_count
    for index, width in enumerate(configured_widths[:column_count]):
        widths[index] = max(6.0, min(float(width), 32.0))
    for row in rows:
        for index in range(column_count):
            value = row[index] if index < len(row) else ""
            display_length = max(len(str(value).replace("\n", " ")), 0)
            widths[index] = min(max(widths[index], display_length * 0.72 + 2), 32.0)
    return widths
