"""Reusable entries, exits and proxy chains."""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"


def upgrade():
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(sa.Column("chain_ids", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("chain_mode", sa.String(10), nullable=False, server_default="native"))
    op.create_table("entries", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("name", sa.String(80), nullable=False),
                    sa.Column("subscription_id", sa.String(32), sa.ForeignKey("subscriptions.id"), nullable=False),
                    sa.Column("mode", sa.String(10), nullable=False), sa.Column("node_key", sa.String(64)),
                    sa.Column("enabled", sa.Boolean(), nullable=False))
    op.create_table("exits", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("name", sa.String(80), nullable=False),
                    sa.Column("config_cipher", sa.Text(), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False))
    op.create_table("chains", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("name", sa.String(80), nullable=False),
                    sa.Column("entry_id", sa.String(32), sa.ForeignKey("entries.id"), nullable=False),
                    sa.Column("exit_id", sa.String(32), sa.ForeignKey("exits.id"), nullable=False),
                    sa.Column("enabled", sa.Boolean(), nullable=False),
                    sa.Column("relay_enabled", sa.Boolean(), nullable=False),
                    sa.Column("listen_port", sa.Integer(), nullable=False, unique=True),
                    sa.Column("public_host", sa.String(253), nullable=False),
                    sa.Column("username", sa.String(80), nullable=False),
                    sa.Column("password_cipher", sa.Text(), nullable=False))


def downgrade():
    for table in ("chains", "exits", "entries"):
        op.drop_table(table)
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("chain_mode")
        batch.drop_column("chain_ids")
