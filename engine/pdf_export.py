import io
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

def create_timetables_pdf(section_timetables, faculty_timetables=None):
    """
    Generate a PDF with one timetable per page for sections and faculty.
    Returns the PDF as bytes.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        rightMargin=30, leftMargin=30,
        topMargin=30, bottomMargin=30
    )

    elements = []
    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        name='TitleCentered',
        parent=styles['Heading1'],
        alignment=TA_CENTER,
        fontSize=14,
        spaceAfter=6,
    )
    subtitle_style = ParagraphStyle(
        name='SubtitleCentered',
        parent=styles['Normal'],
        alignment=TA_CENTER,
        fontSize=9,
        textColor=colors.grey,
        spaceAfter=10,
    )

    # Cell style for table body
    cell_style = ParagraphStyle(
        name='CellCentered',
        parent=styles['Normal'],
        alignment=TA_CENTER,
        fontSize=8,
        leading=10,
    )

    header_style = ParagraphStyle(
        name='HeaderCentered',
        parent=styles['Normal'],
        alignment=TA_CENTER,
        fontSize=9,
        fontName='Helvetica-Bold',
        textColor=colors.whitesmoke,
    )

    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    SLOTS = ["S1 (9:00–9:55)", "S2 (9:55–10:50)", "S3 (11:05–12:00)",
             "S4 (12:00–12:50)", "S5 (1:45–2:40)", "S6 (2:40–3:35)", "S7 (3:35–4:30)"]

    header_row = [Paragraph("<b>Day</b>", header_style)] + [
        Paragraph(f"<b>{slot}</b>", header_style) for slot in SLOTS
    ]

    # 782 usable width: 72 for Day, 101 each for 7 slots
    col_widths = [72] + [101] * 7

    # Alternating row colours for readability
    ROW_COLOURS = [colors.HexColor("#F9F9F9"), colors.HexColor("#EAEAEA")]

    def build_table(grid, header_colour):
        data = [header_row]
        for d_idx, day_name in enumerate(DAYS):
            row = [Paragraph(f"<b>{day_name}</b>", cell_style)]
            for t_idx in range(len(SLOTS)):
                raw = grid[d_idx][t_idx] if grid[d_idx][t_idx] else "—"
                cell_text = raw.replace("\n", "<br/>")
                row.append(Paragraph(cell_text, cell_style))
            data.append(row)

        t = Table(data, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            # Header row
            ('BACKGROUND',  (0, 0), (-1, 0), header_colour),
            ('ALIGN',       (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN',      (0, 0), (-1, -1), 'MIDDLE'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 10),
            ('TOPPADDING',    (0, 0), (-1, 0), 8),
            # Day-name column
            ('BACKGROUND',  (0, 1), (0, -1), colors.HexColor("#D0D0D0")),
            ('FONTNAME',    (0, 1), (0, -1), 'Helvetica-Bold'),
            # Grid lines
            ('GRID',        (0, 0), (-1, -1), 0.5, colors.grey),
            # Row padding
            ('TOPPADDING',    (0, 1), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ]
        # Alternating row background
        for row_idx in range(1, len(data)):
            bg = ROW_COLOURS[(row_idx - 1) % 2]
            style_cmds.append(('BACKGROUND', (1, row_idx), (-1, row_idx), bg))

        t.setStyle(TableStyle(style_cmds))
        return t

    # --- Section timetables ---
    if section_timetables:
        for sec in sorted(section_timetables.keys()):
            elements.append(Paragraph(f"Section Timetable: {sec}", title_style))
            elements.append(Spacer(1, 16))
            elements.append(build_table(section_timetables[sec], colors.HexColor("#37474F")))
            elements.append(PageBreak())

    # --- Faculty timetables ---
    if faculty_timetables:
        for fac in sorted(faculty_timetables.keys()):
            elements.append(Paragraph(f"Faculty Timetable", title_style))
            elements.append(Paragraph(fac, subtitle_style))
            elements.append(Spacer(1, 12))
            elements.append(build_table(faculty_timetables[fac], colors.HexColor("#1565C0")))
            elements.append(PageBreak())

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
