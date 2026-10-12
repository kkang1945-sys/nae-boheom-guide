"""initial local insurance schema

Revision ID: 20260902_0001
Revises: None
"""

from alembic import op
import sqlalchemy as sa


revision = "20260902_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "insurance_documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_name", sa.String(255), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("insurer_candidate", sa.String(120), nullable=True),
        sa.Column("product_candidate", sa.String(500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "coverage_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("document_id", sa.Integer(), sa.ForeignKey("insurance_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_sequence", sa.Integer(), nullable=False),
        sa.Column("row_sequence", sa.Integer(), nullable=False),
        sa.Column("extracted_name", sa.Text(), nullable=False),
        sa.Column("extracted_amount", sa.String(100), nullable=False),
        sa.Column("extracted_amount_won", sa.Integer(), nullable=True),
        sa.Column("extracted_period", sa.Text(), nullable=False),
        sa.Column("extracted_renewal", sa.Boolean(), nullable=False),
        sa.Column("compensation_type", sa.String(30), nullable=False),
        sa.Column("benefit_description", sa.Text(), nullable=False),
        sa.Column("confirmed_name", sa.Text(), nullable=True),
        sa.Column("confirmed_amount_won", sa.Integer(), nullable=True),
        sa.Column("confirmed_period", sa.Text(), nullable=True),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("document_id", "table_sequence", "row_sequence"),
    )


def downgrade() -> None:
    op.drop_table("coverage_records")
    op.drop_table("insurance_documents")
