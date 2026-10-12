"""Keep user scope decisions for document-level condition evidence."""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0009"
down_revision = "20260904_0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("policy_condition_evidence_revisions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("evidence_id", sa.Integer(), sa.ForeignKey("policy_condition_evidence.id", ondelete="CASCADE"), nullable=False),
        sa.Column("before_status", sa.String(30), nullable=False),
        sa.Column("after_status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table("policy_condition_evidence_revisions")
