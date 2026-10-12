"""Persist source-bound automatic transcription audits."""
from alembic import op
import sqlalchemy as sa
revision = '20260903_0004'
down_revision = '20260903_0003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pdf_audits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('insurance_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('version', sa.String(120), nullable=False),
        sa.Column('input_fingerprint', sa.String(64), nullable=False),
        sa.Column('report', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('pdf_audits')
