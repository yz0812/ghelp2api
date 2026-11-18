"""Persist the latest successful source change report."""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"


def upgrade():
    op.add_column("sources", sa.Column("refresh_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sources", sa.Column("impact_status", sa.String(16), nullable=False, server_default="baseline"))
    op.add_column("sources", sa.Column("impact_time", sa.Float(), nullable=True))
    op.add_column("sources", sa.Column("impacted_chains", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sources", sa.Column("impact_report", sa.JSON(), nullable=True))
    op.execute("UPDATE sources SET refresh_version = 1 WHERE last_fetch IS NOT NULL")


def downgrade():
    with op.batch_alter_table("sources") as batch:
        for name in ("impact_report", "impacted_chains", "impact_time", "impact_status", "refresh_version"):
            batch.drop_column(name)
