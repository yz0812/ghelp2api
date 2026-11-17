"""Persist complete probe feedback in the shared fingerprint cache."""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"


def upgrade():
    op.add_column("proxy_checks", sa.Column("result", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("proxy_checks") as batch:
        batch.drop_column("result")
