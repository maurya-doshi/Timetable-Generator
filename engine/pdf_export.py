import io
from reportlab.lib.pagesizes import landscape, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

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
    
    title_style = styles['Heading1']
    title_style.alignment = TA_CENTER
    
    # Create a custom style for table cells
    cell_style = ParagraphStyle(
        name='CellCentered',
        parent=styles['Normal'],
        alignment=TA_CENTER,
        fontSize=9,
        leading=11
    )
    
    header_style = ParagraphStyle(
        name='HeaderCentered',
        parent=styles['Normal'],
        alignment=TA_CENTER,
        fontSize=10,
        fontName='Helvetica-Bold',
        textColor=colors.whitesmoke
    )
    
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    SLOTS = ["S1 (9:00–9:55)", "S2 (9:55–10:50)", "S3 (11:05–12:00)",
             "S4 (12:00–12:50)", "S5 (1:45–2:40)", "S6 (2:40–3:35)", "S7 (3:35–4:30)"]
    
    # Header row
    header = [Paragraph("<b>Day</b>", header_style)] + [Paragraph(f"<b>{slot}</b>", header_style) for slot in SLOTS]
    
    # Calculate column widths (total width of A4 landscape is approx 842 points)
    # 842 - 60 (margins) = 782 available width
    # 1 column for Day (approx 70), 7 columns for slots (approx 100 each)
    col_widths = [70] + [100] * 7
    
    # Helper function to generate table
    def build_table(grid, bg_color):
        data = [header]
        for d_idx, day_name in enumerate(DAYS):
            row = [Paragraph(f"<b>{day_name}</b>", cell_style)]
            for t_idx in range(len(SLOTS)):
                cell_text = grid[d_idx][t_idx] if grid[d_idx][t_idx] else "-"
                cell_text = cell_text.replace("\n", "<br/>")
                row.append(Paragraph(cell_text, cell_style))
            data.append(row)
            
        t = Table(data, colWidths=col_widths)
        t.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), bg_color),
            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('BOTTOMPADDING', (0,0), (-1,0), 12),
            ('BACKGROUND', (0,1), (0,-1), colors.lightgrey),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('TOPPADDING', (0,1), (-1,-1), 6),
            ('BOTTOMPADDING', (0,1), (-1,-1), 6),
        ]))
        return t

    # Process section timetables
    if section_timetables:
        for sec in sorted(section_timetables.keys()):
            elements.append(Paragraph(f"Section Timetable: {sec}", title_style))
            elements.append(Spacer(1, 20))
            elements.append(build_table(section_timetables[sec], colors.dimgrey))
            elements.append(PageBreak())
        
    # Process faculty timetables
    if faculty_timetables:
        for fac in sorted(faculty_timetables.keys()):
            elements.append(Paragraph(f"Faculty Timetable: {fac}", title_style))
            elements.append(Spacer(1, 20))
            elements.append(build_table(faculty_timetables[fac], colors.steelblue))
            elements.append(PageBreak())
            
    # Build PDF
    doc.build(elements)
    
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
