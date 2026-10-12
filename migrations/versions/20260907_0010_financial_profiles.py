"""Add subject-level economic inputs without inferred defaults."""
from alembic import op
import sqlalchemy as sa

revision = "20260907_0010"
down_revision = "20260904_0009"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("financial_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("analysis_subjects.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("monthly_essential_expenses_won", sa.Integer(), nullable=True),
        sa.Column("monthly_net_income_won", sa.Integer(), nullable=True),
        sa.Column("income_gap_months", sa.Integer(), nullable=True),
        sa.Column("emergency_fund_available_won", sa.Integer(), nullable=True),
        sa.Column("emergency_fund_to_use_won", sa.Integer(), nullable=True),
        sa.Column("paid_leave_support_won", sa.Integer(), nullable=True),
        sa.Column("loan_balance_won", sa.Integer(), nullable=True),
        sa.Column("dependent_count", sa.Integer(), nullable=True),
        sa.Column("family_monthly_expenses_won", sa.Integer(), nullable=True),
        sa.Column("family_support_months", sa.Integer(), nullable=True),
        sa.Column("liquid_assets_to_use_won", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("financial_profiles")
