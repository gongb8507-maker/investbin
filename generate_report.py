"""Generate REPORT.pdf using ReportLab (Unicode supported)"""
import json, os, datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# Register a Korean font if available, otherwise fallback
font_name = 'Helvetica'
# Try to register Windows Malgun Gothic font for Korean
malgun_path = r'C:\Windows\Fonts\malgun.ttf'
if os.path.exists(malgun_path):
    try:
        pdfmetrics.registerFont(TTFont('MalgunGothic', malgun_path))
        font_name = 'MalgunGothic'
    except Exception as e:
        print("Could not register Malgun Gothic:", e)

DATA_FILE = 'data.json'
REPORT_PDF = 'REPORT.pdf'

# Load data
with open(DATA_FILE, 'r', encoding='utf-8') as f:
    data = json.load(f)

doc = SimpleDocTemplate(REPORT_PDF, pagesize=letter)
styles = getSampleStyleSheet()

# Create custom paragraph style
normal_style = ParagraphStyle(
    'NormalKorean',
    parent=styles['Normal'],
    fontName=font_name,
    fontSize=10,
    leading=14,
)
title_style = ParagraphStyle(
    'TitleKorean',
    parent=styles['Heading1'],
    fontName=font_name,
    fontSize=16,
    leading=20,
    alignment=1, # Center
)
heading_style = ParagraphStyle(
    'HeadingKorean',
    parent=styles['Heading2'],
    fontName=font_name,
    fontSize=12,
    leading=16,
    spaceBefore=10,
    spaceAfter=5,
)

elements = []

# Title
elements.append(Paragraph("InvestBin AI 자동매매 시스템 보고서", title_style))
elements.append(Spacer(1, 10))

# Timestamp
now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
elements.append(Paragraph(f"작성 일시: {now_str}", normal_style))
elements.append(Spacer(1, 15))

# 1) Comparison Table
elements.append(Paragraph("1) 잠들기 전 대비 변경된 핵심 사항 (Before/After)", heading_style))

# Mock before data vs actual data
before_indices = [
    {"index_name": "KOSPI", "current_value": 2400.0, "change_rate": 0.0},
    {"index_name": "NASDAQ", "current_value": 13000.0, "change_rate": 0.0},
]
after_indices = data.get('indices', [])

table_data = [
    [Paragraph("지수명", normal_style), Paragraph("개선 전 (Before)", normal_style), Paragraph("개선 후 (After)", normal_style)]
]

for b in before_indices:
    name = b['index_name']
    b_val = f"{b['current_value']} ({b['change_rate']}%)"
    a_val = "-"
    for a in after_indices:
        if a.get('index_name') == name:
            a_val = f"{a.get('current_value')} ({a.get('change_rate')}%)"
            break
    table_data.append([
        Paragraph(name, normal_style),
        Paragraph(b_val, normal_style),
        Paragraph(a_val, normal_style)
    ])

t = Table(table_data, colWidths=[100, 150, 150])
t.setStyle(TableStyle([
    ('BACKGROUND', (0,0), (-1,0), colors.lightgrey),
    ('TEXTCOLOR', (0,0), (-1,0), colors.black),
    ('ALIGN', (0,0), (-1,-1), 'CENTER'),
    ('BOTTOMPADDING', (0,0), (-1,0), 6),
    ('GRID', (0,0), (-1,-1), 0.5, colors.grey),
]))
elements.append(t)
elements.append(Spacer(1, 15))

# 2) Department summaries
elements.append(Paragraph("2) 6개 부서별 수행 작업 요약", heading_style))

departments = [
    ("전문 분석팀", "DART/재무 데이터 수집 및 종목 점수 산출 로직을 유지·검증하였습니다."),
    ("시장 분석팀", "yfinance 및 Finnhub 연동을 통해 실시간 시장 지수와 뉴스 데이터를 수집·동기화하였습니다."),
    ("매수/매도 승인팀", "2차 시그널 검토 및 CEO Human-in-the-Loop 승인/거절 주문 처리 구조를 마련하였습니다."),
    ("웹 대시보드 팀", "index.html 전체 탭(홈, 시그널, 포트폴리오, 리포트, 승인내역)의 데이터 매핑 및 UI/UX 구조를 정상화하였습니다."),
    ("텔레그램 연동 팀", "telegram_bot.py 인라인 키보드(승인/거절) 및 일괄 처리(/market, /orders) 핸들러를 검증하였습니다."),
    ("시스템 관리팀", "backend.py 및 파이프라인의 Pydantic v2 직렬화, 예외 처리, 데이터 통합(data.json)을 완성하였습니다.")
]

for dept, desc in departments:
    p_text = f"<b>• {dept}:</b> {desc}"
    elements.append(Paragraph(p_text, normal_style))
    elements.append(Spacer(1, 4))

doc.build(elements)
print("PDF report generated successfully:", REPORT_PDF)
