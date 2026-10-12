"""Delete app-managed analysis data while preserving originals outside uploads."""
from __future__ import annotations

from insurance_app.documents import delete_document_data, list_documents


def deletion_summary(session) -> dict:
    documents = list_documents(session)
    managed = [doc for doc in documents if str(doc.source_path).startswith(str(session.info.get("upload_dir", "")))]
    return {"documents": documents, "document_count": len(documents), "managed_count": len(managed)}


def delete_all_analysis_data(session, upload_dir) -> int:
    documents = list_documents(session)
    for document in documents:
        delete_document_data(session, document.id, upload_dir, delete_managed_original=True)
    from insurance_app.models import AnalysisSubject
    for subject in list(session.scalars(__import__("sqlalchemy").select(AnalysisSubject))):
        session.delete(subject)
    session.flush()
    return len(documents)
