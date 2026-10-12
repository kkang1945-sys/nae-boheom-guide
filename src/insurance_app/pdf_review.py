from insurance_app.models import PdfCoverage, PdfCoverageRevision

CHOICES = {
    'renewal': {'unknown', 'renewable', 'nonrenewable'},
    'contract_role': {'unknown', 'main', 'rider'},
    'compensation_type': {'unknown', 'fixed', 'actual_cost'},
    'payment_frequency': {'unknown', 'once', 'per_event', 'per_day', 'repeat'},
}


def save_pdf_review(session, coverage_id, document_id, values):
    row = session.get(PdfCoverage, coverage_id)
    if row is None or row.document_id != document_id:
        raise LookupError('담보를 찾을 수 없습니다.')
    for key, allowed in CHOICES.items():
        if values.get(key) not in allowed:
            raise ValueError('선택한 담보 정보가 올바르지 않습니다.')
    for key in ('name', 'insurance_period', 'payment_period'):
        if not isinstance(values.get(key), str) or not values[key].strip():
            raise ValueError('담보명과 보험기간·납입기간을 입력해 주세요.')
        values[key] = values[key].strip()
    amount = values.get('amount_won')
    if type(amount) is not int or not 0 <= amount <= 9_000_000_000_000_000:
        raise ValueError('금액을 올바른 원 단위로 입력해 주세요.')
    # Only editable fields enter user-confirmed data; original extraction stays immutable.
    saved = {key: values[key] for key in (*CHOICES, 'name', 'amount_won', 'insurance_period', 'payment_period')}
    if row.confirmed_fields != saved:
        row.revisions.append(PdfCoverageRevision(before=row.confirmed_fields, after=saved))
    row.confirmed_fields = saved
    row.review_status = 'needs_review' if any(saved[k] == 'unknown' for k in CHOICES) else 'confirmed'
    session.flush()
    return row
