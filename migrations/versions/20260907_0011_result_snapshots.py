"""Store versioned analysis result snapshots."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0011"
down_revision = "20260907_0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("analysis_result_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("analysis_subjects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rule_version", sa.String(length=80), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("analysis_result_snapshots")
