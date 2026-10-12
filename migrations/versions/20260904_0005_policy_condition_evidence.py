"""Store source-linked policy condition evidence without payout conclusions."""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0005"
down_revision = "20260903_0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "policy_condition_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("insurance_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("coverage_id", sa.Integer(), sa.ForeignKey("pdf_coverages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("source_bbox", sa.JSON(), nullable=True),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("extracted_detail", sa.JSON(), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("extractor_version", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_id", "coverage_id", "category", "source_fingerprint"),
    )


def downgrade():
    op.drop_table("policy_condition_evidence")
