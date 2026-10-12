"""Separate PDF candidates, source rectangles and user review history."""
from alembic import op
import sqlalchemy as sa

revision = '20260903_0003'
down_revision = '20260903_0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('pdf_extraction_runs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('insurance_documents.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('version', sa.String(80), nullable=False),
        sa.Column('warnings', sa.JSON(), nullable=False),
        sa.Column('row_count', sa.Integer(), nullable=False))
    op.create_table('pdf_coverages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('document_id', sa.Integer(), sa.ForeignKey('insurance_documents.id', ondelete='CASCADE'), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=False),
        sa.Column('row_number', sa.Integer(), nullable=False),
        sa.Column('extracted_fields', sa.JSON(), nullable=False),
        sa.Column('raw_cells', sa.JSON(), nullable=False),
        sa.Column('cell_boxes', sa.JSON(), nullable=False),
        sa.Column('bbox', sa.JSON(), nullable=False),
        sa.Column('confirmed_fields', sa.JSON(), nullable=True),
        sa.Column('review_status', sa.String(30), nullable=False),
        sa.UniqueConstraint('document_id', 'page_number', 'row_number'))
    op.create_table('pdf_coverage_revisions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('coverage_id', sa.Integer(), sa.ForeignKey('pdf_coverages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('pdf_coverage_revisions')
    op.drop_table('pdf_coverages')
    op.drop_table('pdf_extraction_runs')
