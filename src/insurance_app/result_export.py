"""Local result exports; no source document or external service is accessed."""
import csv
import os
from io import StringIO, BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from insurance_app.privacy_masking import mask_document_name, mask_personal_information, mask_subject_label

STATUS_LABELS = {"pending": "판단 보류", "no_gap": "계산 공백 없음", "partial_gap": "일부 자금공백", "large_gap": "큰 자금공백"}


def _safe_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def result_csv(subject, report) -> str:
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["분석 대상", "위험", "상태", "필요 자금(원)", "대응 자금(원)", "자금공백(원)", "규칙 버전", "판단 보류 이유", "검토 필요 규모(원)", "핵심 안내"])
    scenarios = report["scenarios"] or [{"standard_name": "확정 담보 없음", "status": "pending", "reason": "계산에 쓸 확정 담보가 없습니다."}]
    for item in scenarios:
        writer.writerow([_safe_cell(value) for value in [mask_subject_label(subject.label), item["standard_name"],
            STATUS_LABELS.get(item["status"], item["status"]), item.get("required_won", ""),
            item.get("available_won", ""), item.get("gap_won", ""), report["version"],
            item.get("reason") or ", ".join(item.get("missing", [])),
            item.get("review_amount_won", item.get("gap_won", "")), item.get("headline", "")]])
    return "\ufeff" + stream.getvalue()


def result_pdf(label, report, *, evidence=None, saved_at=None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph

    font_path = Path(os.environ.get("INSURANCE_PDF_FONT", "C:/Windows/Fonts/malgun.ttf"))
    if not font_path.is_file():
        raise ValueError("PDF 한글 글꼴을 찾을 수 없습니다. INSURANCE_PDF_FONT에 한글 TTF 경로를 설정해 주세요.")
    if "InsuranceKorean" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("InsuranceKorean", str(font_path)))
    styles = {
        "body": ParagraphStyle("body", fontName="InsuranceKorean", fontSize=10, leading=16, spaceAfter=7, wordWrap="CJK"),
        "title": ParagraphStyle("title", fontName="InsuranceKorean", fontSize=23, leading=32, spaceAfter=18, textColor=colors.HexColor("#173653")),
        "heading": ParagraphStyle("heading", fontName="InsuranceKorean", fontSize=14, leading=21, spaceBefore=14, spaceAfter=9, keepWithNext=True),
    }
    story = []
    def add(text, style="body"):
        story.append(Paragraph(escape(str(text).replace("−", "-")).replace("\n", "<br/>"), styles[style]))
    def money(value):
        return "입력 없음" if value is None else f"{value:,}원"
    add("내 보험 · 자금공백 분석", "title")
    add(f"분석 대상: {mask_subject_label(label)}")
    add(f"저장 당시 결과 · {saved_at}" if saved_at else "현재 입력 기준 결과 · 내보내기는 결과 저장과 별개입니다.")
    add(f"계산 규칙: {report['version']}")
    add(report.get("scope", "보험금 지급 여부를 판단하지 않습니다."))
    add("위험별 결과", "heading")
    for item in report["scenarios"]:
        priority = f"우선 검토 {item['priority_rank']}순위 · " if item.get("priority_rank") else ""
        add(f"{priority}{item['standard_name']} · {STATUS_LABELS.get(item['status'], item['status'])}", "heading")
        if item["status"] == "pending":
            add(item.get("reason") or "확인할 정보: " + ", ".join(item.get("missing", [])))
        else:
            if item.get("headline"):
                add(item["headline"])
            add(f"필요 자금 {money(item.get('required_won'))} / 대응 자금 {money(item.get('available_won'))}")
            review_amount = item.get('review_amount_won', item.get('gap_won'))
            add(f"추정 자금공백 {money(item.get('gap_won'))} · 검토 필요 규모 {money(review_amount)}")
            if item.get("guidance"):
                add(item["guidance"])
            if item.get("plain_explanation"):
                add(item["plain_explanation"])
            add("공백 = max(0, 필요 자금 - 대응 자금). 서로 다른 위험의 금액은 합산하지 않습니다.")
            add("큰 자금공백 표시는 해당 생애단계의 월 시나리오 필요액 3개월분 이상이라는 앱의 보조 표시 기준입니다. 공식 권장 가입금액이 아닙니다.")
        if item.get("duration_scenarios"):
            add("기간별 비교 · 앱의 독립 시나리오이며 권장기간이 아닙니다.")
            for duration in item["duration_scenarios"]:
                add(f"{duration['months']}개월: " + ("판단 보류" if duration['status'] == 'pending' else f"필요 자금 {money(duration['required_won'])} / 공백 {money(duration['gap_won'])}"))
        for source in item.get("items", []):
            add(f"원문: {mask_document_name(source['document_name'])} · {source['page_number']}쪽 · {mask_personal_information(source['coverage_name'])} · {money(source.get('amount_won'))}")
    if not report["scenarios"]:
        add("판단 보류: 계산에 쓸 확정 담보가 없습니다. 보장이 없거나 자금공백이 0원이라는 뜻이 아닙니다.")
        for item in report.get("fallback", {}).values():
            add("확인할 정보: " + ", ".join(item.get("missing", [])))
    for status in report.get("protection_statuses", []):
        add(status["standard_name"] + " · 보장 현황", "heading")
        add(status["scope"])
        for item in status.get("items", []):
            add(f"원문: {mask_document_name(item['document_name'])} · {item['page_number']}쪽 · {mask_personal_information(item['coverage_name'])}")
    add("사용자 입력과 계산식", "heading")
    if evidence is None:
        add("이 기록에는 저장 당시 입력·근거가 없습니다. 현재 입력으로 대체하지 않았습니다.")
    else:
        context = evidence.get("subject_context")
        if context:
            add(f"생애단계 분석 기준 · {context['stage_label']}", "heading")
            add(context["sex_focus"])
            for focus in context.get("focus", []):
                add(focus)
        for item in evidence["inputs"]:
            value = item["value"]
            unit = "개월" if item["key"].endswith("months") else "명"
            rendered = money(value) if item["key"].endswith("_won") else ("입력 없음" if value is None else f"{value}{unit}")
            add(f"{item['label']}: {rendered}")
        for formula in evidence["formulae"]:
            add(formula)
        add("공적제도 확인 자료", "heading")
        add(evidence.get("public_evidence_scope", "공적제도 지원금은 자금공백에서 차감하지 않았습니다."))
        for program in evidence.get("public_programs", []):
            add(f"{program['title']} · {program['summary']}")
            if program.get("review_reason"):
                add(f"{program.get('relevance', '확인 이유')}: {program['review_reason']}")
            add(f"확인할 점: {program['caveat']}")
            add(f"공식 출처: {program['source_title']} · 확인일 {program['verified_on']}")
            add(program["url"])
    add("계산에서 제외한 문서", "heading")
    for item in report.get("blocked_documents", []):
        add(f"{mask_document_name(item['document_name'])}: {mask_personal_information(item['reason'])}")
    if not report.get("blocked_documents"):
        add("제외한 문서가 없습니다.")
    add("가정과 한계", "heading")
    add("대비기간과 사용 자금은 사용자가 정한 시나리오입니다. 공적제도 지원금을 현금처럼 차감하지 않았으며, 정부·공공기관의 개인별 민간보험 권장액을 뜻하지 않습니다.")
    add("실제 지급 여부와 금액은 약관·면책·감액·갱신·지급횟수에 따라 달라질 수 있습니다. 실손은 정액보험금과 합산하지 않습니다. 공백은 비상자금·저축 등으로도 대비할 수 있으며 보험 가입·해지·감액을 권유하지 않습니다.")
    stream = BytesIO()
    def footer(canvas, doc):
        canvas.setFont("InsuranceKorean", 8)
        canvas.setFillColor(colors.HexColor("#526172"))
        canvas.drawString(40, 24, "개인정보 마스킹본 · 공유 전 내용을 확인해 주세요")
        canvas.drawRightString(doc.pagesize[0] - 40, 24, str(doc.page))
    SimpleDocTemplate(stream, pagesize=(595.28, 841.89), rightMargin=40, leftMargin=40,
                      topMargin=40, bottomMargin=48, title="내 보험 자금공백 분석", author="보험앱").build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()
