"""Add explicit child and retirement scenario inputs without inferred defaults."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0012"
down_revision = "20260907_0011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("financial_profiles") as batch:
        batch.add_column(sa.Column("guardian_monthly_income_loss_won", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("child_monthly_care_cost_won", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("retirement_monthly_income_to_use_won", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("retirement_monthly_care_cost_won", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("financial_profiles") as batch:
        batch.drop_column("retirement_monthly_care_cost_won")
        batch.drop_column("retirement_monthly_income_to_use_won")
        batch.drop_column("child_monthly_care_cost_won")
        batch.drop_column("guardian_monthly_income_loss_won")
