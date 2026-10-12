"""Delete app-managed analysis data while preserving originals outside uploads."""
from sqlalchemy import select

from insurance_app.documents import delete_document_data, list_documents
from insurance_app.models import AnalysisSubject


def delete_all_analysis_data(session, upload_dir) -> int:
    documents = list_documents(session)
    for document in documents:
        delete_document_data(session, document.id, upload_dir, delete_managed_original=True)
    for subject in list(session.scalars(select(AnalysisSubject))):
        session.delete(subject)
    session.flush()
    return len(documents)
