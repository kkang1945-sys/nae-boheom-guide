"""Add one-person analysis subjects without inferring demographic values."""
from alembic import op
import sqlalchemy as sa

revision = "20260904_0006"
down_revision = "20260904_0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "analysis_subjects",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("relationship_to_user", sa.String(30), nullable=False),
        sa.Column("birth_year", sa.Integer(), nullable=True),
        sa.Column("life_stage", sa.String(40), nullable=False),
        sa.Column("economic_role", sa.String(40), nullable=False),
        sa.Column("sex_specific_review", sa.String(40), nullable=False),
        sa.Column("pregnancy_childbirth_review", sa.String(40), nullable=False),
        sa.Column("review_status", sa.String(30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    with op.batch_alter_table("insurance_documents") as batch:
        batch.add_column(sa.Column("subject_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_insurance_documents_subject", "analysis_subjects", ["subject_id"], ["id"], ondelete="SET NULL")


def downgrade():
    with op.batch_alter_table("insurance_documents") as batch:
        batch.drop_constraint("fk_insurance_documents_subject", type_="foreignkey")
        batch.drop_column("subject_id")
    op.drop_table("analysis_subjects")
