"""pdf_payslip.py - build a PDF payslip (bytes) from a payslip dict."""

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def _m(x):
    return f"{x:,.2f}"


def build_payslip_pdf(slip, company):
    buf = BytesIO()
    month_label = datetime.strptime(slip["month"], "%Y-%m").strftime("%B %Y")
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"Payslip {month_label} - {slip['name']}",
    )
    styles = getSampleStyleSheet()
    lop_days = format(slip["lop_days"].normalize(), "f")

    info = Table(
        [
            ["Employee", slip["name"], "Employee ID", str(slip["emp_id"])],
            ["Department", slip["department"] or "-", "Designation", slip["designation"] or "-"],
            ["Working days", str(slip["working_days"]), "Loss-of-pay days", lop_days],
        ],
        colWidths=[32 * mm, 52 * mm, 36 * mm, 50 * mm],
    )
    info.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    rows = [
        ["Earnings", "Amount", "Deductions", "Amount"],
        ["Basic", _m(slip["basic"]), "Provident fund", _m(slip["pf"])],
        ["HRA", _m(slip["hra"]), "Income tax (est.)", _m(slip["tax"])],
        ["Allowances", _m(slip["allowances"]), "Loss-of-pay", _m(slip["lop_deduction"])],
        ["Bonus / overtime", _m(slip["bonus"]), "Other deductions", _m(slip["other_ded"])],
        ["Gross earnings", _m(slip["gross"]), "Total deductions", _m(slip["total_ded"])],
        ["NET PAY", "", "", _m(slip["net_pay"])],
    ]
    pay = Table(rows, colWidths=[45 * mm, 38 * mm, 50 * mm, 37 * mm])
    pay.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5e9f0")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 5), (-1, 6), "Helvetica-Bold"),
        ("BACKGROUND", (0, 6), (-1, 6), colors.HexColor("#dbe7ff")),
        ("SPAN", (0, 6), (2, 6)),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))

    doc.build([
        Paragraph(f"<b>{escape(company)}</b>", styles["Title"]),
        Paragraph(f"Payslip for {month_label}", styles["Heading3"]),
        Spacer(1, 6),
        info,
        Spacer(1, 12),
        pay,
        Spacer(1, 14),
        Paragraph("This is a system-generated payslip. Income tax is an estimate "
                  "based on regular monthly pay.", styles["Italic"]),
    ])
    return buf.getvalue()
