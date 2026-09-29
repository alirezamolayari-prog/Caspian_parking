"""PDF export via Qt (QTextDocument + QPdfWriter): right-to-left HTML table, A4 landscape."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from caspian_parking.core.digits import to_persian_digits
from caspian_parking.services.exporters import EXPORT_FONT, ExportTable

HEADER_BG = "#DCE9E8"
SECTION_BG = "#F2F2F2"
BORDER = "#999999"


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, int) and not isinstance(value, bool):
        return to_persian_digits(f"{value:,}".replace(",", "٬"))
    return html.escape(str(value))


def table_html(table: ExportTable, chart: dict[str, Any] | None = None) -> str:
    columns = len(table.columns)
    parts = [
        f'<html><body dir="rtl" style="font-family:{EXPORT_FONT}; font-size:9pt;">',
        f'<h2 align="center">{html.escape(table.title)}</h2>',
    ]
    if table.subtitle:
        parts.append(f'<p align="center">{html.escape(table.subtitle).replace(chr(10), "<br/>")}</p>')
    parts.append(
        f'<table dir="rtl" width="100%" cellspacing="0" cellpadding="4" border="1" style="border-color:{BORDER};">'
    )
    parts.append("<tr>" + "".join(f'<th bgcolor="{HEADER_BG}">{html.escape(c)}</th>' for c in table.columns) + "</tr>")
    for section in table.sections:
        if section.title:
            parts.append(
                f'<tr><td colspan="{columns}" bgcolor="{SECTION_BG}"><b>{html.escape(section.title)}</b></td></tr>'
            )
        for row in section.rows:
            parts.append("<tr>" + "".join(f"<td>{_cell(v)}</td>" for v in row) + "</tr>")
    parts.append("</table>")
    if table.footer:
        parts.append(f"<p>{html.escape(table.footer)}</p>")
    del chart  # the heatmap is already in the table rows
    parts.append("</body></html>")
    return "".join(parts)


def export_pdf(table: ExportTable, path: Path, chart: dict[str, Any] | None = None) -> Path:
    from PySide6.QtCore import QMarginsF, Qt
    from PySide6.QtGui import QPageLayout, QPageSize, QPainter, QPdfWriter, QTextDocument

    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    writer.setPageLayout(
        QPageLayout(QPageSize(QPageSize.PageSizeId.A4), QPageLayout.Orientation.Landscape, QMarginsF(12, 12, 12, 12))
    )
    writer.setResolution(150)
    document = QTextDocument()
    document.setDefaultTextOption(document.defaultTextOption())
    option = document.defaultTextOption()
    option.setTextDirection(Qt.LayoutDirection.RightToLeft)
    document.setDefaultTextOption(option)
    document.setHtml(table_html(table, chart))
    painter = QPainter(writer)
    try:
        page_rect = writer.pageLayout().paintRectPixels(writer.resolution())
        document.setPageSize(page_rect.size().toSizeF())
        pages = max(1, document.pageCount())
        for page in range(pages):
            if page:
                writer.newPage()
            painter.save()
            painter.translate(0, -page * page_rect.height())
            document.drawContents(painter, page_rect.toRectF().translated(0, page * page_rect.height()))
            painter.restore()
    finally:
        painter.end()
    return path
