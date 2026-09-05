import io
import datetime
from decimal import Decimal
from django.core.files.base import ContentFile
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
)
from core.models import Payslip


class PayslipPDFGenerator:
    """
    Generates high-quality, professional PDF payslips for employees using ReportLab.
    Produces a branded document including employee details, bank details,
    earnings/deductions itemization, and net payable summary.
    """

    # Corporate styling palette
    PRIMARY_COLOR = colors.HexColor("#1E293B")   # Deep Slate
    SECONDARY_COLOR = colors.HexColor("#2563EB") # Royal Blue
    ACCENT_COLOR = colors.HexColor("#0F766E")    # Deep Teal
    BG_LIGHT = colors.HexColor("#F8FAFC")        # Soft Slate Background
    BORDER_COLOR = colors.HexColor("#E2E8F0")    # Border Gray
    TEXT_DARK = colors.HexColor("#0F172A")
    TEXT_MUTED = colors.HexColor("#64748B")

    @classmethod
    def generate_pdf_bytes(cls, payslip: Payslip) -> bytes:
        """Renders the payslip into in-memory PDF bytes."""
        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=letter,
            rightMargin=36,
            leftMargin=36,
            topMargin=36,
            bottomMargin=36
        )

        styles = getSampleStyleSheet()

        # Custom paragraph styles
        company_style = ParagraphStyle(
            'CompanyHeader',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=22,
            leading=26,
            textColor=cls.PRIMARY_COLOR
        )
        tagline_style = ParagraphStyle(
            'CompanyTagline',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=12,
            textColor=cls.TEXT_MUTED
        )
        title_style = ParagraphStyle(
            'DocTitle',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=14,
            leading=18,
            textColor=cls.SECONDARY_COLOR,
            alignment=2 # Right aligned
        )
        subtitle_style = ParagraphStyle(
            'DocSubtitle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=9,
            leading=12,
            textColor=cls.TEXT_MUTED,
            alignment=2
        )
        section_heading = ParagraphStyle(
            'SectionHeading',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=11,
            leading=14,
            textColor=cls.PRIMARY_COLOR,
            spaceAfter=4
        )
        meta_label = ParagraphStyle(
            'MetaLabel',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=11,
            textColor=cls.TEXT_MUTED
        )
        meta_value = ParagraphStyle(
            'MetaValue',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=11,
            textColor=cls.TEXT_DARK
        )
        tbl_header = ParagraphStyle(
            'TableHeader',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=colors.white
        )
        tbl_cell = ParagraphStyle(
            'TableCell',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=10,
            textColor=cls.TEXT_DARK
        )
        tbl_cell_bold = ParagraphStyle(
            'TableCellBold',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=cls.TEXT_DARK
        )
        tbl_cell_right = ParagraphStyle(
            'TableCellRight',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=8,
            leading=10,
            textColor=cls.TEXT_DARK,
            alignment=2
        )
        tbl_cell_right_bold = ParagraphStyle(
            'TableCellRightBold',
            parent=styles['Normal'],
            fontName='Helvetica-Bold',
            fontSize=8,
            leading=10,
            textColor=cls.TEXT_DARK,
            alignment=2
        )

        elements = []

        # 1. Header with Company Name and Document Title
        emp = payslip.employee
        payrun = payslip.payrun
        contract = payslip.contract

        header_table = Table([
            [
                Paragraph("<b>PeoplePay360</b>", company_style),
                Paragraph("CONFIDENTIAL SALARY PAYSLIP", title_style)
            ],
            [
                Paragraph("HR & Payroll Intelligent Enterprise Suite", tagline_style),
                Paragraph(f"Period: <b>{payslip.period_start}</b> to <b>{payslip.period_end}</b>", subtitle_style)
            ]
        ], colWidths=[280, 260])
        header_table.setStyle(TableStyle([
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
        ]))
        elements.append(header_table)
        elements.append(Spacer(1, 10))
        elements.append(HRFlowable(width="100%", thickness=1.5, color=cls.PRIMARY_COLOR, spaceBefore=2, spaceAfter=12))

        # 2. Employee & Employment Information Box
        bank_masked = (
            f"••••{emp.bank_account_number[-4:]}" if emp.bank_account_number and len(emp.bank_account_number) >= 4
            else (emp.bank_account_number or "Not Provided")
        )

        emp_info_data = [
            [
                Paragraph("Employee Name:", meta_label), Paragraph(f"<b>{emp.full_name}</b>", meta_value),
                Paragraph("Contract Name:", meta_label), Paragraph(contract.name, meta_value),
            ],
            [
                Paragraph("Employee ID:", meta_label), Paragraph(f"<b>{emp.code}</b>", meta_value),
                Paragraph("Salary Structure:", meta_label), Paragraph(payslip.salary_structure.name, meta_value),
            ],
            [
                Paragraph("Department:", meta_label), Paragraph(emp.department or "N/A", meta_value),
                Paragraph("Working Schedule:", meta_label), Paragraph(contract.working_schedule.name, meta_value),
            ],
            [
                Paragraph("Job Title:", meta_label), Paragraph(emp.job_title or "N/A", meta_value),
                Paragraph("Contract Base Wage:", meta_label), Paragraph(f"${contract.wage:,.2f} ({contract.get_wage_type_display()})", meta_value),
            ],
            [
                Paragraph("Bank Name:", meta_label), Paragraph(emp.bank_name or "N/A", meta_value),
                Paragraph("Worked Days in Period:", meta_label), Paragraph(f"{payslip.worked_days} Days", meta_value),
            ],
            [
                Paragraph("Bank Account:", meta_label), Paragraph(bank_masked, meta_value),
                Paragraph("Payslip Status:", meta_label), Paragraph(f"<b>{payslip.get_state_display().upper()}</b>", meta_value),
            ],
        ]

        emp_info_table = Table(emp_info_data, colWidths=[90, 180, 100, 170])
        emp_info_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), cls.BG_LIGHT),
            ('BOX', (0, 0), (-1, -1), 1, cls.BORDER_COLOR),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, cls.BORDER_COLOR),
            ('TOPPADDING', (0, 0), (-1, -1), 4),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ]))
        elements.append(emp_info_table)
        elements.append(Spacer(1, 16))

        # 3. Itemized Salary Rules (Earnings & Deductions)
        elements.append(Paragraph("SALARY BREAKDOWN & ITEMIZATION", section_heading))

        line_headers = [
            Paragraph("Rule Code", tbl_header),
            Paragraph("Salary Component", tbl_header),
            Paragraph("Category", tbl_header),
            Paragraph("Rate (%)", tbl_header),
            Paragraph("Base Amount", tbl_header),
            Paragraph("Total Amount", tbl_header),
        ]

        table_rows = [line_headers]
        lines = payslip.lines.all().order_by('sequence', 'id')

        for idx, line in enumerate(lines):
            is_summary = line.category in ('GROSS', 'NET')
            r_style = tbl_cell_bold if is_summary else tbl_cell
            r_right = tbl_cell_right_bold if is_summary else tbl_cell_right

            table_rows.append([
                Paragraph(line.code, r_style),
                Paragraph(line.name, r_style),
                Paragraph(line.category, r_style),
                Paragraph(f"{line.rate:.2f}%", r_right),
                Paragraph(f"${line.amount:,.2f}", r_right),
                Paragraph(f"${line.total:,.2f}", r_right),
            ])

        lines_table = Table(table_rows, colWidths=[70, 160, 80, 60, 85, 85])
        lines_style = [
            ('BACKGROUND', (0, 0), (-1, 0), cls.PRIMARY_COLOR),
            ('ALIGN', (0, 0), (-1, 0), 'LEFT'),
            ('ALIGN', (3, 0), (-1, -1), 'RIGHT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('TOPPADDING', (0, 0), (-1, -1), 5),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
            ('LEFTPADDING', (0, 0), (-1, -1), 6),
            ('RIGHTPADDING', (0, 0), (-1, -1), 6),
            ('BOX', (0, 0), (-1, -1), 1, cls.PRIMARY_COLOR),
            ('INNERGRID', (0, 0), (-1, -1), 0.5, cls.BORDER_COLOR),
        ]

        # Alternating row colors and highlight GROSS / NET
        for row_idx, line in enumerate(lines, start=1):
            if line.category == 'GROSS':
                lines_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor("#EFF6FF")))
            elif line.category == 'NET':
                lines_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), colors.HexColor("#ECFDF5")))
            elif row_idx % 2 == 0:
                lines_style.append(('BACKGROUND', (0, row_idx), (-1, row_idx), cls.BG_LIGHT))

        lines_table.setStyle(TableStyle(lines_style))
        elements.append(lines_table)
        elements.append(Spacer(1, 14))

        # 4. Net Salary Callout Box
        net_summary_data = [
            [
                Paragraph("TOTAL GROSS EARNINGS:", ParagraphStyle('NetLbl', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=cls.PRIMARY_COLOR)),
                Paragraph(f"<b>${payslip.gross_wage:,.2f}</b>", ParagraphStyle('NetVal', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=cls.PRIMARY_COLOR, alignment=2)),
            ],
            [
                Paragraph("TOTAL DEDUCTIONS:", ParagraphStyle('DedLbl', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#B91C1C"))),
                Paragraph(f"<b>-${payslip.total_deductions:,.2f}</b>", ParagraphStyle('DedVal', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=10, textColor=colors.HexColor("#B91C1C"), alignment=2)),
            ],
            [
                Paragraph("NET SALARY PAYABLE:", ParagraphStyle('FinalNetLbl', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=13, textColor=cls.ACCENT_COLOR)),
                Paragraph(f"<b>${payslip.net_wage:,.2f}</b>", ParagraphStyle('FinalNetVal', parent=styles['Normal'], fontName='Helvetica-Bold', fontSize=15, textColor=cls.ACCENT_COLOR, alignment=2)),
            ]
        ]
        summary_table = Table(net_summary_data, colWidths=[360, 180])
        summary_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor("#F0FDF4")),
            ('BOX', (0, 0), (-1, -1), 1.5, cls.ACCENT_COLOR),
            ('LINEBELOW', (0, 1), (-1, 1), 1, cls.ACCENT_COLOR),
            ('TOPPADDING', (0, 0), (-1, -1), 6),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
            ('LEFTPADDING', (0, 0), (-1, -1), 10),
            ('RIGHTPADDING', (0, 0), (-1, -1), 10),
        ]))
        elements.append(summary_table)
        elements.append(Spacer(1, 20))

        # 5. Footer & Legal Disclaimers
        footer_text = (
            f"Generated on {datetime.date.today().strftime('%B %d, %Y')} • "
            f"Payrun: {payrun.name} • "
            f"This is a system-generated document created by PeoplePay360. No physical signature required."
        )
        elements.append(Paragraph(footer_text, ParagraphStyle(
            'FooterStyle',
            parent=styles['Normal'],
            fontName='Helvetica',
            fontSize=7,
            textColor=cls.TEXT_MUTED,
            alignment=1
        )))

        # Build document
        doc.build(elements)
        pdf_data = buffer.getvalue()
        buffer.close()
        return pdf_data

    @classmethod
    def generate_and_save(cls, payslip: Payslip) -> Payslip:
        """
        Renders PDF, saves it to payslip.pdf_file (in media/payslips/YYYY/MM/),
        and saves the payslip instance.
        """
        pdf_bytes = cls.generate_pdf_bytes(payslip)
        year_str = payslip.period_start.strftime("%Y")
        month_str = payslip.period_start.strftime("%m")
        filename = f"Payslip_{payslip.employee.code}_{year_str}_{month_str}.pdf"

        payslip.pdf_file.save(filename, ContentFile(pdf_bytes), save=True)
        return payslip
