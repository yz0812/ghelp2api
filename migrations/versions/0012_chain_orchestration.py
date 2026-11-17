"""Ordered proxy paths, diagnostic results and monitoring history."""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"


def upgrade():
    with op.batch_alter_table("chains") as batch:
        batch.alter_column("entry_id", existing_type=sa.String(32), nullable=True)
        batch.alter_column("exit_id", existing_type=sa.String(32), nullable=True)
        batch.add_column(sa.Column("paths", sa.JSON(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(sa.Column("lock_exit", sa.Boolean(), nullable=False, server_default=sa.true()))
        batch.add_column(sa.Column("interval", sa.Integer(), nullable=False, server_default="300"))
        batch.add_column(sa.Column("monitor_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("jobs", sa.Column("result", sa.JSON(), nullable=True))
    op.create_table("chain_templates", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("name", sa.String(80), nullable=False), sa.Column("config", sa.JSON(), nullable=False),
                    sa.Column("created_at", sa.Float(), nullable=False))
    op.create_table("chain_samples", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("chain_id", sa.String(32), sa.ForeignKey("chains.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("tested_at", sa.Float(), nullable=False),
                    sa.Column("fingerprint", sa.String(64), nullable=False), sa.Column("result", sa.JSON(), nullable=False))
    op.create_table("chain_events", sa.Column("id", sa.String(32), primary_key=True),
                    sa.Column("chain_id", sa.String(32), sa.ForeignKey("chains.id", ondelete="CASCADE"), nullable=False),
                    sa.Column("created_at", sa.Float(), nullable=False),
                    sa.Column("kind", sa.String(32), nullable=False), sa.Column("detail", sa.JSON(), nullable=False))
    for table, column in (("chain_samples", "chain_id"), ("chain_samples", "tested_at"),
                          ("chain_events", "chain_id"), ("chain_events", "created_at")):
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade():
    for table in ("chain_events", "chain_samples", "chain_templates"):
        op.drop_table(table)
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("result")
    with op.batch_alter_table("chains") as batch:
        for column in ("paths", "revision", "lock_exit", "interval", "monitor_enabled"):
            batch.drop_column(column)
    # New paths cannot be represented by the old schema; production rollback restores its backup.
