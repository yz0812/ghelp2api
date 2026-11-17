"""Allow a fixed entry pool while preserving legacy single-node entries."""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"


def upgrade():
    op.add_column("entries", sa.Column("node_keys", sa.JSON(), nullable=False, server_default="[]"))
    entries = sa.table("entries", sa.column("id", sa.String), sa.column("mode", sa.String),
                       sa.column("node_key", sa.String), sa.column("node_keys", sa.JSON))
    connection = op.get_bind()
    for row in connection.execute(sa.select(entries.c.id, entries.c.node_key).where(
            entries.c.mode == "fixed", entries.c.node_key.is_not(None))).mappings():
        connection.execute(entries.update().where(entries.c.id == row["id"]).values(node_keys=[row["node_key"]]))


def downgrade():
    # A legacy entry can represent only one member of the selected pool.
    with op.batch_alter_table("entries") as batch:
        batch.drop_column("node_keys")
