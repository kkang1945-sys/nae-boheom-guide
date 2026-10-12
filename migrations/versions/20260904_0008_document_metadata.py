"""Separate document metadata candidates, evidence and user-confirmed values."""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0008"
down_revision = "20260904_0007"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("insurance_documents") as batch:
        batch.add_column(sa.Column("metadata_evidence", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("confirmed_insurer", sa.String(120), nullable=True))
        batch.add_column(sa.Column("confirmed_product", sa.String(500), nullable=True))
        batch.add_column(sa.Column("metadata_review_status", sa.String(30), nullable=False, server_default="needs_review"))
    op.create_table("document_metadata_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("insurance_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("before", sa.JSON(), nullable=True),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("document_metadata_revisions")
    with op.batch_alter_table("insurance_documents") as batch:
        batch.drop_column("metadata_review_status")
        batch.drop_column("confirmed_product")
        batch.drop_column("confirmed_insurer")
        batch.drop_column("metadata_evidence")
