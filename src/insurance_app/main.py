from contextlib import asynccontextmanager
import logging
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from insurance_app.config import load_settings
from insurance_app.database import Base, create_schema, create_session_factory
from insurance_app.documents import (
    DocumentUploadError,
    confirm_coverage,
    delete_document_data,
    get_document,
    list_documents,
    register_hwpml,
    reextract_pdf_document,
    store_uploaded_document,
)
from insurance_app.formatting import format_korean_won, readable_ocr_text
from insurance_app import models  # noqa: F401 - 모델을 스키마에 등록
from insurance_app.pdf_reader import PdfReadError, render_page
from insurance_app.documents import file_sha256
from insurance_app.pdf_review import save_pdf_review
from insurance_app.pdf_audit import audit_document, current_audit
from insurance_app.policy_conditions import auto_check_coverage_evidence, auto_check_document_scope, condition_readiness, save_document_condition_reviews
from insurance_app.subjects import assign_contract_group, assign_document, save_subject, subject_context
from insurance_app.ocr import OcrError, current_ocr, ocr_page
from insurance_app.document_metadata import confirm_document_metadata, detect_metadata_candidates
from insurance_app.analysis_preflight import analysis_preflight
from insurance_app.contract_duplicates import duplicate_contract_candidates, same_contract_groups
from insurance_app.query_layout import query_kind, query_layout_ready
from insurance_app.ocr_policy_coverage import extract_kyobo_image_policy
from insurance_app.coverage_classification import classification_groups, RULE_VERSION
from insurance_app.integration_readiness import integration_readiness
from insurance_app.portfolio_integration import portfolio_integration
from insurance_app.coverage_explanations import (
    VERSION as EXPLANATION_VERSION,
    explanation_groups,
    plain_coverage_explanation,
)
from insurance_app.financial_profiles import (
    financial_readiness, save_financial_profile, transient_financial_profile,
)
from insurance_app.subject_funding_results import subject_funding_results
from insurance_app.benefit_usage import benefit_usage_summary
from insurance_app.public_demo import public_demo_report, seed_public_demo
from insurance_app.result_snapshots import save_result_snapshot
from insurance_app.result_comparison import compare_reports
from insurance_app.result_evidence import result_evidence
from insurance_app.data_deletion import delete_all_analysis_data
from insurance_app.result_export import STATUS_LABELS, result_csv, result_pdf
from insurance_app.public_evidence import PUBLIC_EVIDENCE_SCOPE, public_program_evidence
from insurance_app.hira_disease import HiraDiseaseClient, HiraDiseaseError, SOURCE_URL as HIRA_SOURCE_URL
from insurance_app.privacy_masking import (
    mask_document_name,
    mask_personal_information,
    mask_subject_label,
    sensitive_values,
    sensitive_word_boxes,
)


PACKAGE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
templates.env.filters["korean_won"] = format_korean_won
templates.env.filters["readable_ocr"] = readable_ocr_text
templates.env.filters["mask_pii"] = mask_personal_information
templates.env.filters["mask_document_name"] = mask_document_name
templates.env.filters["mask_subject_label"] = mask_subject_label
settings = load_settings(Path.cwd())
session_factory = create_session_factory(settings.database_path)
templates.env.globals["public_demo"] = settings.public_demo


@asynccontextmanager
async def lifespan(_: FastAPI):
    create_schema(session_factory)
    if settings.public_demo:
        with session_factory.begin() as session:
            seed_public_demo(session)
    from insurance_app.file_deletion import recover_deletions
    recover_deletions(settings.upload_dir, session_factory.kw["bind"])
    yield

app = FastAPI(
    title="내 보험 이해하기",
    description="보험을 판매하거나 특정 상품을 추천하지 않는 로컬 보험 분석 앱",
    lifespan=lifespan,
)


@app.middleware("http")
async def local_privacy_headers(request: Request, call_next):
    """Keep local personal data out of caches and reject browser cross-origin writes."""
    if settings.public_demo and request.method not in {"GET", "HEAD", "OPTIONS"} and request.url.path != "/mock-analysis":
        return Response(
            "공개 시연에서는 보험증권 업로드와 데이터 변경을 사용할 수 없습니다.",
            status_code=403, media_type="text/plain; charset=utf-8",
        )
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        expected_origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
        same_origin = not origin or origin.rstrip("/") == expected_origin.rstrip("/")
        if origin and not same_origin:
            supplied = urlsplit(origin)
            expected = urlsplit(expected_origin)
            loopback_names = {"127.0.0.1", "localhost", "::1"}
            loopback_alias = (
                supplied.scheme == expected.scheme
                and supplied.hostname in loopback_names
                and expected.hostname in loopback_names
                and supplied.port == expected.port
            )
            opaque_local_origin = (
                origin == "null"
                and expected.hostname in loopback_names
                and request.headers.get("sec-fetch-site") == "same-origin"
            )
            same_origin = loopback_alias or opaque_local_origin
        if not same_origin:
            logger.warning(
                "Blocked cross-origin write: origin=%r expected=%r fetch_site=%r referer=%r",
                origin,
                expected_origin,
                request.headers.get("sec-fetch-site"),
                request.headers.get("referer"),
            )
            return Response(
                "다른 사이트에서 보낸 변경 요청을 차단했습니다.",
                status_code=403,
                media_type="text/plain; charset=utf-8",
            )
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    with session_factory() as session:
        documents = list_documents(session)
        subjects = list(session.scalars(select(models.AnalysisSubject).options(
            selectinload(models.AnalysisSubject.documents)).order_by(models.AnalysisSubject.id)))
    duplicate_reports = duplicate_contract_candidates(documents)
    duplicate_ids = {item for report in duplicate_reports for item in report['document_ids']}
    same_contract_ids = {item for report in duplicate_reports if report.get('action') == 'treat_as_one_contract'
                         for item in report['document_ids']}
    possible_duplicate_ids = duplicate_ids - same_contract_ids
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"documents": documents, "subjects": subjects, "duplicate_ids": duplicate_ids,
                 "same_contract_ids": same_contract_ids,
                 "possible_duplicate_ids": possible_duplicate_ids,
                 "duplicate_reports": duplicate_reports, "message": request.query_params.get("message")},
    )


@app.get("/privacy", response_class=HTMLResponse)
def privacy_notice(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="workflow.html",
        context={
            "title": "개인정보와 원본 보호",
            "step": "안내",
            "body": ("공개 시연판은 합성 샘플만 사용하며 실제 보험증권 업로드를 받지 않습니다."
                     if settings.public_demo else
                     "보험증권과 분석 데이터는 이 PC 안에 저장됩니다. 외부 AI로 보내지 않으며, 앱에 직접 추가한 파일은 삭제 화면에서 원본과 분석 데이터를 함께 지울 수 있습니다."),
            "next_url": "/",
            "next_label": "보험증권 목록으로",
        },
    )


@app.get("/data/delete", response_class=HTMLResponse)
def data_delete_page(request: Request):
    with session_factory() as session:
        documents = list_documents(session)
        subject_count = session.query(models.AnalysisSubject).count()
    return templates.TemplateResponse(request=request, name="data_delete.html", context={
        "documents": documents, "subject_count": subject_count,
    }, headers={"Cache-Control": "no-store"})


@app.post("/data/delete")
def data_delete(confirm_delete: str = Form()):
    if confirm_delete != "DELETE_ALL":
        raise HTTPException(422, "전체 삭제 확인 문구가 필요합니다.")
    with session_factory.begin() as session:
        count = delete_all_analysis_data(session, settings.upload_dir)
    message = f"분석 데이터 {count}개 문서를 삭제했습니다."
    if session.info.get("deletion_pending"):
        message += " 일부 원본 파일 삭제가 남았습니다. 앱 재시작 시 다시 시도합니다."
    return RedirectResponse(f"/?message={message}", status_code=303)


@app.get("/financial-info", response_class=HTMLResponse)
def financial_info(request: Request) -> HTMLResponse:
    with session_factory() as session:
        subjects = list(session.scalars(select(models.AnalysisSubject).options(
            selectinload(models.AnalysisSubject.financial_profile)).order_by(models.AnalysisSubject.id)))
        readiness = {subject.id: financial_readiness(subject.financial_profile, subject.life_stage)
                     for subject in subjects}
    return templates.TemplateResponse(request=request, name="financial_info.html",
        context={"subjects": subjects, "readiness": readiness,
                 "message": request.query_params.get("message")}, headers={"Cache-Control": "no-store"})


@app.post("/subjects/{subject_id}/financial-profile")
async def financial_profile_save(request: Request, subject_id: int):
    form = await request.form()
    try:
        with session_factory.begin() as session:
            subject = session.get(models.AnalysisSubject, subject_id)
            if subject is None:
                raise HTTPException(404, "분석 대상자를 찾을 수 없습니다.")
            save_financial_profile(session, subject, dict(form))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse("/financial-info?message=경제정보를 저장했습니다.", status_code=303)


@app.get("/mock-analysis", response_class=HTMLResponse)
def mock_analysis_form(request: Request) -> HTMLResponse:
    with session_factory() as session:
        documents = [document for document in list_documents(session) if document.format == "pdf"]
    return templates.TemplateResponse(request=request, name="mock_analysis.html",
        context={"documents": documents, "error": None, "values": {}},
        headers={"Cache-Control": "no-store"})


@app.post("/mock-analysis", response_class=HTMLResponse)
async def mock_analysis_result(request: Request) -> HTMLResponse:
    from types import SimpleNamespace

    form = dict(await request.form())
    # The quick check asks only for facts a user already knows. Duration is not
    # presented as a recommendation: one month is used internally and the UI
    # reports how many months the confirmed money can cover instead.
    if "income_gap_months" not in form:
        form["income_gap_months"] = "1"
    if "emergency_fund_to_use_won" not in form:
        form["emergency_fund_to_use_won"] = form.get("emergency_fund_available_won", "0")
    if "paid_leave_support_won" not in form:
        form["paid_leave_support_won"] = "0"
    if "family_monthly_expenses_won" not in form:
        form["family_monthly_expenses_won"] = form.get("monthly_essential_expenses_won", "")
    if "liquid_assets_to_use_won" not in form:
        form["liquid_assets_to_use_won"] = form.get("emergency_fund_available_won", "0")
    try:
        raw_document_ids = form.get("document_ids", "")
        document_ids = {int(value) for value in raw_document_ids.split(",") if value.strip()}
        if not document_ids and form.get("document_id"):
            document_ids = {int(form["document_id"])}
        if not document_ids:
            raise ValueError("분석할 보험증권이 없습니다.")
        profile = transient_financial_profile(form)
        hospital_fields = {
            "hospitalization_months": (24, "입원·휴직 기간"),
            "hospital_monthly_maintained_income_won": (2_000_000_000, "입원 중 유지되는 월 소득"),
            "hospital_additional_cost_won": (2_000_000_000, "입원 중 추가비용"),
            "hospital_paid_leave_support_won": (2_000_000_000, "유급휴가·회사 지원"),
        }
        for field, (maximum, label) in hospital_fields.items():
            try:
                value = int(form.get(field, "0") or 0)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{label}을(를) 숫자로 입력해 주세요.") from exc
            if not 0 <= value <= maximum:
                raise ValueError(f"{label}을(를) 올바른 범위로 입력해 주세요.")
            setattr(profile, field, value)
    except (TypeError, ValueError) as exc:
        with session_factory() as session:
            documents = [document for document in list_documents(session) if document.format == "pdf"]
        message = str(exc) if str(exc) else "분석할 보험증권을 선택해 주세요."
        return templates.TemplateResponse(request=request, name="mock_analysis.html",
            context={"documents": documents, "error": message, "values": form}, status_code=422,
            headers={"Cache-Control": "no-store"})
    subject = SimpleNamespace(id=-1, label="저장하지 않은 모의분석", review_status="confirmed",
                              life_stage="working", financial_profile=profile)
    with session_factory() as session:
        documents = list_documents(session)
        selected_ids = {item.id for item in documents if item.id in document_ids
                        and item.format == "pdf"}
        if selected_ids != document_ids:
            raise HTTPException(404, "분석할 PDF 보험증권을 찾을 수 없습니다.")
        report = (public_demo_report(profile, [item for item in documents if item.id in selected_ids])
                  if settings.public_demo else
                  subject_funding_results(subject, documents, transient_document_ids=selected_ids))
    return templates.TemplateResponse(request=request, name="funding_results.html", context={
        "subjects": [], "selected": subject, "report": report, "message": None,
        "snapshot": None, "transient": True,
    }, headers={"Cache-Control": "no-store"})


@app.get("/results", response_class=HTMLResponse)
def results(request: Request, subject_id: int | None = None) -> HTMLResponse:
    with session_factory() as session:
        subjects = list(session.scalars(select(models.AnalysisSubject).options(
            selectinload(models.AnalysisSubject.financial_profile),
            selectinload(models.AnalysisSubject.result_snapshots)).order_by(models.AnalysisSubject.id)))
        selected = next((subject for subject in subjects if subject.id == subject_id), None)
        if subject_id is not None and selected is None:
            raise HTTPException(404, "분석 대상자를 찾을 수 없습니다.")
        report = subject_funding_results(selected, list_documents(session)) if selected else None
    return templates.TemplateResponse(request=request, name="funding_results.html", context={
        "subjects": subjects, "selected": selected, "report": report,
        "message": request.query_params.get("message"), "snapshot": None,
    }, headers={"Cache-Control": "no-store"})


@app.post("/subjects/{subject_id}/results/snapshot")
def result_snapshot_save(subject_id: int):
    with session_factory.begin() as session:
        subject = session.get(models.AnalysisSubject, subject_id)
        if subject is None:
            raise HTTPException(404, "분석 대상자를 찾을 수 없습니다.")
        report = subject_funding_results(subject, list_documents(session))
        snapshot = save_result_snapshot(session, subject, report)
    return RedirectResponse(f"/results?subject_id={subject_id}&message=결과 기준을 저장했습니다. 기록 #{snapshot.id}", status_code=303)


@app.get("/subjects/{subject_id}/results/export.csv")
def result_export_csv(subject_id: int):
    with session_factory() as session:
        subject = session.get(models.AnalysisSubject, subject_id)
        if subject is None:
            raise HTTPException(404, "분석 대상자를 찾을 수 없습니다.")
        content = result_csv(subject, subject_funding_results(subject, list_documents(session)))
    return Response(content=content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="insurance-result-{subject_id}.csv"',
                             "Cache-Control": "no-store"})


@app.get("/results/snapshots/{snapshot_id}", response_class=HTMLResponse)
def result_snapshot_view(request: Request, snapshot_id: int):
    with session_factory() as session:
        snapshot = session.scalar(select(models.AnalysisResultSnapshot).where(
            models.AnalysisResultSnapshot.id == snapshot_id).options(
                selectinload(models.AnalysisResultSnapshot.subject).selectinload(
                    models.AnalysisSubject.result_snapshots)))
        if snapshot is None:
            raise HTTPException(404, "저장된 결과를 찾을 수 없습니다.")
        selected = snapshot.subject
        subjects = [selected]
    return templates.TemplateResponse(request=request, name="funding_results.html", context={
        "subjects": subjects, "selected": selected, "report": snapshot.report,
        "message": None, "snapshot": snapshot,
    }, headers={"Cache-Control": "no-store"})


@app.get("/results/snapshots/{snapshot_id}/compare", response_class=HTMLResponse)
def result_snapshot_compare(request: Request, snapshot_id: int):
    with session_factory() as session:
        snapshot = session.scalar(select(models.AnalysisResultSnapshot).where(
            models.AnalysisResultSnapshot.id == snapshot_id).options(
                selectinload(models.AnalysisResultSnapshot.subject).selectinload(
                    models.AnalysisSubject.financial_profile)))
        if snapshot is None:
            raise HTTPException(404, "저장된 결과를 찾을 수 없습니다.")
        current = subject_funding_results(snapshot.subject, list_documents(session))
        comparison = compare_reports(snapshot.report, current)
    return templates.TemplateResponse(request=request, name="result_comparison.html", context={
        "snapshot": snapshot, "subject": snapshot.subject, "comparison": comparison,
        "status_labels": STATUS_LABELS | {"not_present": "해당 결과 없음"},
    }, headers={"Cache-Control": "no-store"})


@app.get("/subjects/{subject_id}/evidence", response_class=HTMLResponse)
def subject_result_evidence(request: Request, subject_id: int):
    with session_factory() as session:
        subject = session.scalar(select(models.AnalysisSubject).where(
            models.AnalysisSubject.id == subject_id).options(
                selectinload(models.AnalysisSubject.financial_profile)))
        if subject is None:
            raise HTTPException(404, "분석 대상자를 찾을 수 없습니다.")
        report = subject_funding_results(subject, list_documents(session))
        evidence = result_evidence(subject, report)
    return templates.TemplateResponse(request=request, name="result_evidence.html", context={
        "subject": subject, "evidence": evidence,
    }, headers={"Cache-Control": "no-store"})


@app.get("/evidence", response_class=HTMLResponse)
def evidence(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name="public_evidence.html",
        context={
            "programs": public_program_evidence(),
            "scope": PUBLIC_EVIDENCE_SCOPE,
        },
    )


@app.get("/medical-statistics", response_class=HTMLResponse)
def medical_statistics(request: Request, sick_code: str = "", year: int | None = None,
                       view: str = "inpatient_outpatient") -> HTMLResponse:
    result = None
    error = None
    allowed_views = {"inpatient_outpatient", "gender_age"}
    if view not in allowed_views:
        raise HTTPException(422, "지원하지 않는 통계 구분입니다.")
    if sick_code or year is not None:
        if not sick_code or year is None:
            error = "질병코드와 조회 연도를 모두 입력해 주세요."
        elif not settings.hira_disease_api_key:
            error = "심평원 API 키가 아직 이 PC의 환경변수에 설정되지 않았습니다."
        else:
            try:
                client = HiraDiseaseClient(settings.hira_disease_api_key,
                    settings.data_dir / "public_cache" / "hira_disease")
                result = (client.inpatient_outpatient(sick_code, year)
                          if view == "inpatient_outpatient" else client.gender_age(sick_code, year))
            except (ValueError, HiraDiseaseError) as exc:
                error = str(exc)
    columns = []
    if result:
        columns = list(dict.fromkeys(key for item in result["items"] for key in item))
    return templates.TemplateResponse(request=request, name="medical_statistics.html", context={
        "configured": bool(settings.hira_disease_api_key), "result": result, "error": error,
        "columns": columns, "sick_code": sick_code, "year": year, "view": view,
        "source_url": HIRA_SOURCE_URL,
    }, headers={"Cache-Control": "no-store"})


@app.post("/documents")
def document_upload(file: list[UploadFile] = File()) -> RedirectResponse:
    documents = []
    failures = []
    for uploaded_file in file:
        try:
            with session_factory.begin() as session:
                document = store_uploaded_document(
                    session,
                    uploaded_file.file,
                    uploaded_file.filename or "insurance-document",
                    settings.upload_dir,
                    max_bytes=settings.max_upload_bytes,
                )
                if document.format == 'pdf':
                    is_query_layout = bool(query_kind(document))
                    if settings.tesseract_path and settings.tessdata_dir:
                        for page in document.pages:
                            if page.status == 'needs_ocr' or (is_query_layout and page.status != 'text_ready'):
                                try:
                                    ocr_page(session, document, page.page_number,
                                             settings.tesseract_path, settings.tessdata_dir)
                                except OcrError:
                                    # Upload remains recoverable; the page screen exposes a retry.
                                    pass
                    if is_query_layout and query_layout_ready(document):
                        reextract_pdf_document(session, document)
                    if not document.pdf_coverages:
                        extract_kyobo_image_policy(document)
                    report = audit_document(session, document)
                    auto_check_coverage_evidence(document, report)
                    detect_metadata_candidates(document)
                    auto_check_document_scope(document)
                documents.append(document)
        except DocumentUploadError as exc:
            failures.append(f"{mask_document_name(uploaded_file.filename or '이름 없는 파일')}: {exc}")
        finally:
            uploaded_file.file.close()
    if not documents:
        raise HTTPException(status_code=422, detail=" / ".join(failures))
    if len(file) > 1 or failures:
        message = f"{len(documents)}개의 보험증권을 읽었습니다."
        if failures:
            message += " 읽지 못한 파일 · " + " / ".join(failures)
        return RedirectResponse(url=f"/documents/combined?message={quote(message)}", status_code=303)
    document = documents[0]
    suffix = "/summary" if document.format == "pdf" and (current_audit(document) or {}).get("summary_ready") else ""
    return RedirectResponse(url=f"/documents/{document.id}{suffix}", status_code=303)


@app.get("/documents/combined", response_class=HTMLResponse)
def documents_combined(request: Request) -> HTMLResponse:
    with session_factory() as session:
        documents = list_documents(session)
        coverage_summaries = {}
        for document in documents:
            len(document.pages)
            len(document.pdf_coverages)
            len(document.coverages)
            rows = document.pdf_coverages if document.format == "pdf" else document.coverages
            coverage_summaries[document.id] = []
            for row in rows:
                if document.format == "pdf":
                    values = row.confirmed_fields or row.extracted_fields
                    display_amounts = values.get("display_amounts") or []
                    coverage_summaries[document.id].append({
                        "name": values.get("name", ""),
                        "amount_won": (display_amounts[0]["amount_won"] if display_amounts
                                       else values.get("amount_won")),
                        "display_name": (display_amounts[0]["label"] if display_amounts
                                         else values.get("name", "")),
                        "more_amounts": display_amounts[1:],
                    })
                else:
                    coverage_summaries[document.id].append({
                        "name": row.confirmed_name or row.extracted_name,
                        "display_name": row.confirmed_name or row.extracted_name,
                        "amount_won": (row.confirmed_amount_won if row.confirmed_amount_won is not None
                                       else row.extracted_amount_won),
                        "more_amounts": [],
                    })
    return templates.TemplateResponse(
        request=request,
        name="documents_combined.html",
        context={"documents": documents, "coverage_summaries": coverage_summaries,
                 "message": request.query_params.get("message")},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/benefits/{kind}", response_class=HTMLResponse)
def benefit_usage(request: Request, kind: str) -> HTMLResponse:
    try:
        with session_factory() as session:
            report = benefit_usage_summary(list_documents(session), kind)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return templates.TemplateResponse(
        request=request,
        name="benefit_usage.html",
        context={"report": report},
        headers={"Cache-Control": "no-store"},
    )


@app.get("/documents/{document_id}", response_class=HTMLResponse)
def document_detail(request: Request, document_id: int) -> HTMLResponse:
    with session_factory() as session:
        document = get_document(session, document_id)
        audit = current_audit(document) if document else None
        easy_coverages = [{
            "row": row,
            "values": row.confirmed_fields or row.extracted_fields,
            "explanation": plain_coverage_explanation(
                (row.confirmed_fields or row.extracted_fields).get("name", "")),
        } for row in document.pdf_coverages] if document and document.format == "pdf" else []
    if document is None:
        raise HTTPException(status_code=404, detail="보험증권을 찾을 수 없습니다.")
    return templates.TemplateResponse(
        request=request,
        name="document.html",
        context={"document": document, "audit": audit, "easy_coverages": easy_coverages},
    )


@app.post("/coverages/{coverage_id}/confirm")
def coverage_confirm(
    coverage_id: int,
    document_id: int = Form(),
    name: str = Form(),
    amount_won: int = Form(),
    period: str = Form(),
) -> RedirectResponse:
    try:
        with session_factory.begin() as session:
            target = session.get(models.CoverageRecord, coverage_id)
            if target is None or target.document_id != document_id:
                raise HTTPException(status_code=404, detail="담보를 찾을 수 없습니다.")
            coverage = confirm_coverage(
                session, coverage_id, name=name, amount_won=amount_won, period=period
            )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if coverage is None or coverage.document_id != document_id:
        raise HTTPException(status_code=404, detail="담보를 찾을 수 없습니다.")
    return RedirectResponse(url=f"/documents/{document_id}#coverage-{coverage_id}", status_code=303)


@app.get("/documents/{document_id}/pages/{page_number}", response_class=HTMLResponse)
def document_page(request: Request, document_id: int, page_number: int, coverage_id: int | None = None):
    with session_factory() as session:
        document = get_document(session, document_id)
    page = next((p for p in document.pages if p.page_number == page_number), None) if document else None
    if page is None:
        raise HTTPException(404, "원문 페이지를 찾을 수 없습니다.")
    selected = next((c for c in document.pdf_coverages if c.id == coverage_id and c.page_number == page_number), None)
    if coverage_id is not None and selected is None:
        raise HTTPException(404, "이 페이지의 담보를 찾을 수 없습니다.")
    ocr = current_ocr(document, page_number)
    display_text = ocr.text if ocr else page.text
    document_sensitive_values = []
    for source_page in document.pages:
        document_sensitive_values.extend(sensitive_values(source_page.text))
    for source_ocr in document.ocr_results:
        document_sensitive_values.extend(sensitive_values(source_ocr.text))
    return templates.TemplateResponse(request=request, name="page.html", context={
        "document": document, "page": page, "selected": selected, "ocr": ocr,
        "masked_display_text": mask_personal_information(display_text, document_sensitive_values),
        "ocr_available": bool(settings.tesseract_path and settings.tessdata_dir),
    }, headers={"Cache-Control": "no-store"})


@app.get("/documents/{document_id}/pages/{page_number}/image")
def document_page_image(document_id: int, page_number: int, coverage_id: int | None = None):
    with session_factory() as session:
        document = get_document(session, document_id)
    if document is None or document.format != "pdf" or not any(p.page_number == page_number for p in document.pages):
        raise HTTPException(404, "원문 페이지를 찾을 수 없습니다.")
    selected = next((c for c in document.pdf_coverages if c.id == coverage_id and c.page_number == page_number), None)
    if coverage_id is not None and selected is None:
        raise HTTPException(404, "이 페이지의 담보를 찾을 수 없습니다.")
    source = Path(document.source_path)
    if not source.is_file():
        raise HTTPException(410, "보관된 원본 파일이 없습니다.")
    if file_sha256(source) != document.sha256:
        raise HTTPException(409, "원본 파일이 변경되어 저장된 추출값과 대조할 수 없습니다.")
    try:
        ocr = current_ocr(document, page_number)
        document_sensitive_values = []
        for source_page in document.pages:
            document_sensitive_values.extend(sensitive_values(source_page.text))
        for source_ocr in document.ocr_results:
            document_sensitive_values.extend(sensitive_values(source_ocr.text))
        ocr_boxes = sensitive_word_boxes(ocr.words, document_sensitive_values) if ocr else []
        image = render_page(source, page_number, selected.bbox if selected else None, ocr_boxes,
                            document_sensitive_values)
    except PdfReadError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(image, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post('/documents/{document_id}/pages/{page_number}/ocr')
def document_page_ocr(document_id: int, page_number: int, force: str = Form('no')):
    if not settings.tesseract_path or not settings.tessdata_dir:
        raise HTTPException(503, '한국어 로컬 OCR이 준비되지 않았습니다.')
    try:
        with session_factory.begin() as session:
            document = get_document(session, document_id)
            if document is None or document.format != 'pdf':
                raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
            ocr_page(session, document, page_number, settings.tesseract_path,
                     settings.tessdata_dir, force=force == 'yes')
            if query_layout_ready(document) and not any(
                row.confirmed_fields is not None or row.revisions for row in document.pdf_coverages
            ):
                reextract_pdf_document(session, document)
                report = audit_document(session, document)
                auto_check_coverage_evidence(document, report)
                auto_check_document_scope(document)
            detect_metadata_candidates(document)
    except OcrError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f'/documents/{document_id}/pages/{page_number}', status_code=303)


@app.post('/documents/{document_id}/metadata')
def document_metadata_confirm(document_id: int, insurer: str = Form(), product: str = Form(),
                              source_checked: str = Form()):
    if source_checked != 'yes':
        raise HTTPException(422, '원문 대조 확인이 필요합니다.')
    try:
        with session_factory.begin() as session:
            document = get_document(session, document_id)
            if document is None:
                raise HTTPException(404, '보험증권을 찾을 수 없습니다.')
            confirm_document_metadata(session, document, insurer=insurer, product=product)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f'/documents/{document_id}#document-metadata', status_code=303)


@app.post('/documents/{document_id}/pdf-coverages/{coverage_id}/review')
def pdf_coverage_review(document_id: int, coverage_id: int, name: str = Form(),
    amount_won: int = Form(), insurance_period: str = Form(), payment_period: str = Form(),
    renewal: str = Form(), contract_role: str = Form(), compensation_type: str = Form(),
    payment_frequency: str = Form(), source_checked: str = Form()):
    if source_checked != 'yes':
        raise HTTPException(422, '원문 대조 확인이 필요합니다.')
    values = dict(name=name, amount_won=amount_won, insurance_period=insurance_period,
                  payment_period=payment_period, renewal=renewal, contract_role=contract_role,
                  compensation_type=compensation_type, payment_frequency=payment_frequency)
    try:
        with session_factory.begin() as session:
            document = session.get(models.InsuranceDocument, document_id)
            if document is None:
                raise LookupError('보험증권을 찾을 수 없습니다.')
            source = Path(document.source_path)
            if not source.is_file():
                raise HTTPException(410, '보관된 원본 파일이 없어 확인 내용을 저장할 수 없습니다.')
            if file_sha256(source) != document.sha256:
                raise HTTPException(409, '원본 파일이 변경되어 확인 내용을 저장할 수 없습니다.')
            save_pdf_review(session, coverage_id, document_id, values)
            refreshed = get_document(session, document_id)
            report = audit_document(session, refreshed)
            auto_check_coverage_evidence(refreshed, report)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f'/documents/{document_id}#pdf-coverage-{coverage_id}', status_code=303)


@app.post("/documents/{document_id}/delete")
def document_delete(
    document_id: int,
    confirm_delete: str = Form(),
) -> RedirectResponse:
    if confirm_delete != "DELETE":
        raise HTTPException(status_code=422, detail="삭제 확인이 필요합니다.")
    with session_factory.begin() as session:
        deleted = delete_document_data(
            session,
            document_id,
            settings.upload_dir,
            delete_managed_original=True,
        )
    if not deleted:
        raise HTTPException(status_code=404, detail="보험증권을 찾을 수 없습니다.")
    message = "보험증권과 앱의 분석 데이터를 삭제했습니다."
    if session.info.get("deletion_pending"):
        message += " 일부 원본 파일 삭제가 남았습니다. 앱 재시작 시 다시 시도합니다."
    return RedirectResponse(url=f"/?message={message}", status_code=303)


@app.post('/documents/{document_id}/audit')
def document_audit(document_id: int):
    with session_factory.begin() as session:
        document = get_document(session, document_id)
        if document is None or document.format != 'pdf':
            raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
        report = audit_document(session, document)
        auto_check_coverage_evidence(document, report)
    suffix = '/summary' if report['summary_ready'] else ''
    return RedirectResponse(f'/documents/{document_id}{suffix}', status_code=303)


@app.post('/documents/{document_id}/reextract')
def document_reextract(document_id: int):
    try:
        with session_factory.begin() as session:
            document = get_document(session, document_id)
            if document is None:
                raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
            if query_kind(document) and settings.tesseract_path and settings.tessdata_dir:
                for page in document.pages:
                    if not current_ocr(document, page.page_number):
                        ocr_page(session, document, page.page_number,
                                 settings.tesseract_path, settings.tessdata_dir)
            reextract_pdf_document(session, document)
            report = audit_document(session, document)
            auto_check_coverage_evidence(document, report)
            detect_metadata_candidates(document)
            auto_check_document_scope(document)
    except DocumentUploadError as exc:
        raise HTTPException(422, str(exc)) from exc
    suffix = '/summary' if report.get('summary_ready') else ''
    return RedirectResponse(f'/documents/{document_id}{suffix}', status_code=303)


@app.get('/documents/{document_id}/summary', response_class=HTMLResponse)
def document_summary(request: Request, document_id: int):
    with session_factory() as session:
        document = get_document(session, document_id)
        if document is None or document.format != 'pdf':
            raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
        audit = current_audit(document)
    if not audit or not audit['summary_ready']:
        raise HTTPException(409, '현재 원본과 추출값의 자동 검산이 필요합니다. 문서 화면에서 검산을 실행해 주세요.')
    return templates.TemplateResponse(request=request, name='pdf_summary.html',
        context={'document': document, 'audit': audit}, headers={'Cache-Control': 'no-store'})


@app.get('/documents/{document_id}/classification', response_class=HTMLResponse)
def document_classification(request: Request, document_id: int):
    with session_factory() as session:
        document = get_document(session, document_id)
        if document is None or document.format != 'pdf':
            raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
        audit = current_audit(document)
        if not audit or not audit['summary_ready']:
            raise HTTPException(409, '현재 원본과 담보 표의 자동 검산이 필요합니다.')
        groups = classification_groups(document)
        count = len(document.pdf_coverages)
    return templates.TemplateResponse(request=request, name='coverage_classification.html',
        context={'document': document, 'groups': groups, 'count': count, 'version': RULE_VERSION},
        headers={'Cache-Control': 'no-store'})


@app.get('/documents/{document_id}/integration', response_class=HTMLResponse)
def document_integration(request: Request, document_id: int):
    with session_factory() as session:
        document = get_document(session, document_id)
        if document is None or document.format != 'pdf':
            raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
        audit = current_audit(document)
        if not audit or not audit['summary_ready']:
            raise HTTPException(409, '현재 원본과 담보 표의 자동 검산이 필요합니다.')
        duplicates = duplicate_contract_candidates(list_documents(session))
        possible_duplicate = any(document.id in report['document_ids'] for report in duplicates)
        report = integration_readiness(document, possible_duplicate=possible_duplicate)
    return templates.TemplateResponse(request=request, name='integration_readiness.html',
        context={'document': document, 'report': report}, headers={'Cache-Control': 'no-store'})


@app.get('/documents/{document_id}/explanations', response_class=HTMLResponse)
def document_explanations(request: Request, document_id: int):
    with session_factory() as session:
        document = get_document(session, document_id)
        if document is None or document.format != 'pdf':
            raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
        audit = current_audit(document)
        if not audit or not audit['summary_ready']:
            raise HTTPException(409, '현재 원본과 담보 표의 자동 검산이 필요합니다.')
        classified = classification_groups(document)
        groups = explanation_groups(classified)
        outside_count = sum(len(group['rows']) for group in classified if group['name'] not in {
            item['name'] for item in groups
        })
    return templates.TemplateResponse(request=request, name='coverage_explanations.html', context={
        'document': document, 'groups': groups, 'outside_count': outside_count, 'version': EXPLANATION_VERSION,
    }, headers={'Cache-Control': 'no-store'})


@app.get('/documents/{document_id}/conditions', response_class=HTMLResponse)
def document_conditions(request: Request, document_id: int):
    with session_factory() as session:
        document = get_document(session, document_id)
        if document is None or document.format != 'pdf':
            raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
        audit = current_audit(document)
        readiness = condition_readiness(document)
    if not audit or not audit['summary_ready']:
        raise HTTPException(409, '조건을 보기 전에 현재 원본과 담보 표의 자동 검산이 필요합니다.')
    labels = {
        'benefit_text': '보장내용 원문', 'payment_trigger': '지급 조건 문구',
        'payment_limit': '횟수·한도 문구', 'exclusion': '제외·면책 문구',
        'reduction': '감액 문구', 'waiting_period': '보장개시·대기 문구',
        'renewal_condition': '갱신 문구',
    }
    return templates.TemplateResponse(request=request, name='policy_conditions.html', context={
        'document': document, 'readiness': readiness, 'labels': labels,
    }, headers={'Cache-Control': 'no-store'})


@app.post('/documents/{document_id}/conditions/review')
async def document_conditions_review(request: Request, document_id: int):
    form = await request.form()
    decisions = {}
    for key, value in form.multi_items():
        if key.startswith('evidence_'):
            try:
                decisions[int(key.removeprefix('evidence_'))] = str(value)
            except ValueError as exc:
                raise HTTPException(422, '공통 조건 식별자가 올바르지 않습니다.') from exc
    try:
        with session_factory.begin() as session:
            document = get_document(session, document_id)
            if document is None or document.format != 'pdf':
                raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
            save_document_condition_reviews(session, document, decisions)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(f'/documents/{document_id}/conditions', status_code=303)


@app.get('/documents/{document_id}/preflight', response_class=HTMLResponse)
def document_preflight(request: Request, document_id: int):
    with session_factory() as session:
        document = get_document(session, document_id)
        if document is None or document.format != 'pdf':
            raise HTTPException(404, 'PDF를 찾을 수 없습니다.')
        report = analysis_preflight(document)
    return templates.TemplateResponse(request=request, name='analysis_preflight.html', context={
        'document': document, 'report': report,
    }, headers={'Cache-Control': 'no-store'})


@app.get('/subjects', response_class=HTMLResponse)
def subjects_page(request: Request):
    with session_factory() as session:
        subjects = list(session.scalars(select(models.AnalysisSubject).options(
            selectinload(models.AnalysisSubject.documents)).order_by(models.AnalysisSubject.id)))
        documents = list_documents(session)
    contract_groups = same_contract_groups(documents)
    return templates.TemplateResponse(request=request, name='subjects.html', context={
        'subjects': subjects, 'documents': documents,
        'same_contract_groups': contract_groups,
        'grouped_document_ids': {
            document.id for group in contract_groups for document in group
        },
        'subject_contexts': {subject.id: subject_context(subject) for subject in subjects},
    }, headers={'Cache-Control': 'no-store'})


@app.get('/subjects/{subject_id}/integration', response_class=HTMLResponse)
def subject_integration(request: Request, subject_id: int):
    with session_factory() as session:
        subject = session.get(models.AnalysisSubject, subject_id)
        if subject is None:
            raise HTTPException(404, '분석 대상자를 찾을 수 없습니다.')
        documents = list_documents(session)
        report = portfolio_integration(subject, documents)
    return templates.TemplateResponse(request=request, name='portfolio_integration.html',
        context={'subject': subject, 'report': report}, headers={'Cache-Control': 'no-store'})


@app.post('/subjects')
def subject_create(label: str = Form(), relationship_to_user: str = Form(), birth_year: str = Form(''),
                   life_stage: str = Form(), economic_role: str = Form(),
                   sex_specific_review: str = Form(), pregnancy_childbirth_review: str = Form()):
    try:
        parsed_year = int(birth_year) if birth_year.strip() else None
        with session_factory.begin() as session:
            save_subject(session, label=label, relationship_to_user=relationship_to_user,
                         birth_year=parsed_year, life_stage=life_stage, economic_role=economic_role,
                         sex_specific_review=sex_specific_review,
                         pregnancy_childbirth_review=pregnancy_childbirth_review)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse('/subjects', status_code=303)


@app.post('/subjects/{subject_id}')
def subject_update(subject_id: int, label: str = Form(''), relationship_to_user: str = Form(),
                   birth_year: str = Form(''), life_stage: str = Form(), economic_role: str = Form(),
                   sex_specific_review: str = Form(), pregnancy_childbirth_review: str = Form()):
    try:
        parsed_year = int(birth_year) if birth_year.strip() else None
        with session_factory.begin() as session:
            subject = session.get(models.AnalysisSubject, subject_id)
            if subject is None:
                raise LookupError('분석 대상자를 찾을 수 없습니다.')
            save_subject(session, subject=subject, label=label.strip() or subject.label,
                         relationship_to_user=relationship_to_user, birth_year=parsed_year,
                         life_stage=life_stage, economic_role=economic_role,
                         sex_specific_review=sex_specific_review,
                         pregnancy_childbirth_review=pregnancy_childbirth_review)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse('/subjects', status_code=303)


@app.post('/subjects/{subject_id}/delete')
def subject_delete(subject_id: int, confirm_delete: str = Form()):
    if confirm_delete != 'DELETE':
        raise HTTPException(422, '삭제 확인이 필요합니다.')
    with session_factory.begin() as session:
        subject = session.scalar(select(models.AnalysisSubject).where(
            models.AnalysisSubject.id == subject_id).options(selectinload(models.AnalysisSubject.documents)))
        if subject is None:
            raise HTTPException(404, '분석 대상자를 찾을 수 없습니다.')
        for document in subject.documents:
            document.subject = None
        session.delete(subject)
        session.flush()
    return RedirectResponse('/subjects', status_code=303)


@app.post('/documents/{document_id}/subject')
def document_subject_assign(document_id: int, subject_id: int = Form()):
    try:
        with session_factory.begin() as session:
            assign_document(session, document_id, subject_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse('/subjects', status_code=303)


@app.post('/documents/{document_id}/subject-group')
def document_subject_group_assign(document_id: int, subject_id: int = Form()):
    try:
        with session_factory.begin() as session:
            members = assign_contract_group(session, document_id, subject_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return RedirectResponse(
        f'/subjects?message=같은 계약 조회자료 {len(members)}개를 한 대상자에게 연결했습니다.',
        status_code=303,
    )


@app.get("/subjects/{subject_id}/results/export.pdf")
def result_export_pdf(subject_id: int):
    with session_factory() as session:
        subject = session.get(models.AnalysisSubject, subject_id)
        if subject is None:
            raise HTTPException(404, "분석 대상자를 찾을 수 없습니다.")
        report = subject_funding_results(subject, list_documents(session))
        try:
            content = result_pdf(subject.label, report, evidence=result_evidence(subject, report))
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="insurance-result-{subject_id}.pdf"',
        "Cache-Control": "no-store"})


@app.get("/results/snapshots/{snapshot_id}/export.{file_format}")
def snapshot_export(snapshot_id: int, file_format: str):
    from types import SimpleNamespace
    if file_format not in {"pdf", "csv"}:
        raise HTTPException(404, "지원하지 않는 파일 형식입니다.")
    with session_factory() as session:
        snapshot = session.get(models.AnalysisResultSnapshot, snapshot_id)
        if snapshot is None:
            raise HTTPException(404, "저장된 결과를 찾을 수 없습니다.")
        report = snapshot.report
        label = report.get("subject_label", "저장 당시 대상자 이름 미기록")
        try:
            content = result_pdf(label, report, evidence=report.get("evidence"), saved_at=str(snapshot.created_at)) if file_format == "pdf" else result_csv(SimpleNamespace(label=label), report)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
    return Response(content, media_type="application/pdf" if file_format == "pdf" else "text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="insurance-snapshot-{snapshot_id}.{file_format}"',
                             "Cache-Control": "no-store"})
