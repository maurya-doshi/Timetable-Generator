import io
from datetime import datetime, timezone
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

# ---------------------------------------------------------------------------
# Colour palette (#14)
# ---------------------------------------------------------------------------
_LECTURE_CLR   = colors.HexColor("#E8F5E9")   # light green
_TUTORIAL_CLR  = colors.HexColor("#E3F2FD")   # light blue
_PRACTICAL_CLR = colors.HexColor("#FFF3E0")   # light orange
_FREE_CLR_A    = colors.HexColor("#F9F9F9")   # alternating grey 1
_FREE_CLR_B    = colors.HexColor("#EAEAEA")   # alternating grey 2


def _cell_colour(raw: str, row_idx: int) -> colors.Color:
    """Return the background colour for a timetable cell based on event type."""
    if not raw or raw == "—":
        return _FREE_CLR_A if row_idx % 2 == 0 else _FREE_CLR_B
    if "[L]" in raw:
        return _LECTURE_CLR
    if "[T]" in raw:
        return _TUTORIAL_CLR
    if "[P]" in raw or "Co-Fac" in raw:
        return _PRACTICAL_CLR
    return _FREE_CLR_A if row_idx % 2 == 0 else _FREE_CLR_B


def create_timetables_pdf(
    section_timetables,
    faculty_timetables=None,
    semester: str = "",
    academic_year: str = "",
):
    """Generate a PDF with one timetable per page for sections and faculty.

    Parameters
    ----------
    section_timetables : dict  {section: 5x7 grid}
    faculty_timetables : dict  {faculty: 5x7 grid}, optional
    semester           : str   e.g. "Odd" or "Even"  (#13)
    academic_year      : str   e.g. "2025-26"         (#13)

    Returns the PDF as bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30, leftMargin=30,
        topMargin=30, bottomMargin=30,
    )

    elements = []
    styles   = getSampleStyleSheet()

    title_style = ParagraphStyle(
        name="TitleCentered",
        parent=styles["Heading1"],
        alignment=TA_CENTER,
        fontSize=14,
        spaceAfter=3,
    )
    subtitle_style = ParagraphStyle(
        name="SubtitleCentered",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
        textColor=colors.grey,
        spaceAfter=8,
    )
    cell_style = ParagraphStyle(
        name="CellCentered",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=8,
        leading=10,
    )
    header_style = ParagraphStyle(
        name="HeaderCentered",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=9,
        fontName="Helvetica-Bold",
        textColor=colors.whitesmoke,
    )

    DAYS  = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    SLOTS = [
        "S1 (9:00-9:55)", "S2 (9:55-10:50)", "S3 (11:05-12:00)",
        "S4 (12:00-12:50)", "S5 (1:45-2:40)", "S6 (2:40-3:35)", "S7 (3:35-4:30)",
    ]

    header_row = [Paragraph("<b>Day</b>", header_style)] + [
        Paragraph(f"<b>{slot}</b>", header_style) for slot in SLOTS
    ]

    # 782 usable width: 72 for Day, 101 each for 7 slots
    col_widths = [72] + [101] * 7

    # Build the metadata subtitle string (#13)
    meta_parts = []
    if semester:
        meta_parts.append(f"{semester} Semester")
    if academic_year:
        meta_parts.append(academic_year)
    generated_at = datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC")
    meta_parts.append(f"Generated: {generated_at}")
    meta_str = "  |  ".join(meta_parts)

    def build_table(grid, header_colour):
        data = [header_row]
        # Collect cell raws for colouring (#14)
        raw_grid = []
        for d_idx, day_name in enumerate(DAYS):
            row = [Paragraph(f"<b>{day_name}</b>", cell_style)]
            day_raws = []
            for t_idx in range(len(SLOTS)):
                raw = grid[d_idx][t_idx] if grid[d_idx][t_idx] else "—"
                day_raws.append(raw)
                cell_text = raw.replace("\n", "<br/>")
                row.append(Paragraph(cell_text, cell_style))
            data.append(row)
            raw_grid.append(day_raws)

        t = Table(data, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            # Header row
            ("BACKGROUND",    (0, 0), (-1, 0), header_colour),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 10),
            ("TOPPADDING",    (0, 0), (-1, 0), 8),
            # Day-name column
            ("BACKGROUND",    (0, 1), (0, -1), colors.HexColor("#D0D0D0")),
            ("FONTNAME",      (0, 1), (0, -1), "Helvetica-Bold"),
            # Grid lines
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.grey),
            # Row padding
            ("TOPPADDING",    (0, 1), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 1), (-1, -1), 5),
        ]

        # Per-cell background colours based on event type (#14)
        for d_idx, day_raws in enumerate(raw_grid):
            row_num = d_idx + 1   # +1 for header row
            for t_idx, raw in enumerate(day_raws):
                col_num = t_idx + 1   # +1 for Day column
                bg = _cell_colour(raw, d_idx)
                style_cmds.append(("BACKGROUND", (col_num, row_num), (col_num, row_num), bg))

        t.setStyle(TableStyle(style_cmds))
        return t

    # --- Section timetables ---
    if section_timetables:
        for sec in sorted(section_timetables.keys()):
            elements.append(Paragraph(f"Section Timetable: {sec}", title_style))
            elements.append(Paragraph(meta_str, subtitle_style))
            elements.append(Spacer(1, 8))
            elements.append(build_table(section_timetables[sec], colors.HexColor("#37474F")))
            elements.append(PageBreak())

    # --- Faculty timetables ---
    if faculty_timetables:
        for fac in sorted(faculty_timetables.keys()):
            elements.append(Paragraph("Faculty Timetable", title_style))
            elements.append(Paragraph(f"{fac}  |  {meta_str}", subtitle_style))
            elements.append(Spacer(1, 8))
            elements.append(build_table(faculty_timetables[fac], colors.HexColor("#1565C0")))
            elements.append(PageBreak())

    # --- Legend ---
    legend_style = ParagraphStyle(
        name="Legend", parent=styles["Normal"],
        fontSize=8, textColor=colors.grey, alignment=TA_CENTER,
    )
    elements.append(Paragraph(
        "<font color='#388E3C'>■ Lecture</font>   "
        "<font color='#1565C0'>■ Tutorial</font>   "
        "<font color='#E65100'>■ Practical / Co-Fac</font>",
        legend_style,
    ))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
