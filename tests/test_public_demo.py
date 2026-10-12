from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from insurance_app import models
from insurance_app.database import Base
from insurance_app.public_demo import public_demo_report, seed_public_demo


def test_public_demo_seed_is_synthetic_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        seed_public_demo(session)
        seed_public_demo(session)
    with factory() as session:
        documents = list(session.scalars(select(models.InsuranceDocument)))
        assert len(documents) == 2
        assert all(document.source_path.startswith("public-demo://") for document in documents)
        assert sum(len(document.pdf_coverages) for document in documents) == 9


def test_public_demo_report_uses_living_expenses_not_peer_average():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory.begin() as session:
        seed_public_demo(session)
    with factory() as session:
        documents = list(session.scalars(select(models.InsuranceDocument)))
        for document in documents:
            _ = document.pdf_coverages
        profile = SimpleNamespace(
            monthly_essential_expenses_won=3_000_000,
            emergency_fund_to_use_won=3_000_000,
        )
        report = public_demo_report(profile, documents)
    assert report["result_summary"]["runway_label"] == "약 7개월"
    assert report["result_summary"]["critical_count"] == 3
    assert report["scenarios"][-1]["standard_name"] == "상해사망"
    assert report["scenarios"][-1]["reference_amount_won"] == 130_000_000
