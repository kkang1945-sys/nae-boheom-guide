"""Persist source-bound local OCR results per PDF page."""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0007"
down_revision = "20260904_0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("ocr_page_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("insurance_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("engine_version", sa.String(120), nullable=False),
        sa.Column("languages", sa.String(30), nullable=False),
        sa.Column("dpi", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("ocr_page_results")
