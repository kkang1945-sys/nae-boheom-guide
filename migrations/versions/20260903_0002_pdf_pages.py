"""Add local PDF page evidence without changing existing insurance values."""
from alembic import op
import sqlalchemy as sa

revision = "20260903_0002"
down_revision = "20260902_0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("insurance_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("rotation", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("reader_version", sa.String(80), nullable=False),
        sa.UniqueConstraint("document_id", "page_number"),
    )


def downgrade():
    op.drop_table("document_pages")
