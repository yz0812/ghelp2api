"""Persist automatic or pinned entry selection per chain."""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"


def upgrade():
    op.add_column("chains", sa.Column("entry_mode", sa.String(10), nullable=False, server_default="auto"))
    op.add_column("chains", sa.Column("entry_node_key", sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table("chains") as batch:
        batch.drop_column("entry_node_key")
        batch.drop_column("entry_mode")
