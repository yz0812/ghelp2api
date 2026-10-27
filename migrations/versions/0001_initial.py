"""Initial schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None


def upgrade():
    op.create_table("accounts",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("token_cipher", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("token_hint", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("error", sa.Text()), sa.Column("info_error", sa.Text()),
        sa.Column("info", sa.JSON(), nullable=False), sa.Column("pk", sa.Text()),
        sa.Column("first_seen", sa.Float()), sa.Column("last_success", sa.Float()),
        sa.Column("last_attempt", sa.Float()), sa.Column("next_refresh", sa.Float(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False), sa.Column("degraded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_table("nodes",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("account_id", sa.String(32), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("server", sa.Text(), nullable=False), sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("region", sa.String(80), nullable=False), sa.Column("latency", sa.Float()),
        sa.Column("reachable", sa.Boolean()), sa.Column("tested_at", sa.Float()))
    op.create_index("ix_nodes_account_id", "nodes", ["account_id"])
    op.create_table("subscriptions",
        sa.Column("id", sa.String(32), primary_key=True), sa.Column("name", sa.String(80), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("key_cipher", sa.Text(), nullable=False), sa.Column("account_ids", sa.JSON(), nullable=False),
        sa.Column("regions", sa.JSON(), nullable=False), sa.Column("exclude_failed", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False))
    op.create_table("jobs",
        sa.Column("id", sa.String(32), primary_key=True), sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False), sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False), sa.Column("errors", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_table("login_sessions", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("csrf", sa.String(80), nullable=False), sa.Column("expires", sa.Float(), nullable=False))
    op.create_table("preferences", sa.Column("key", sa.String(80), primary_key=True),
        sa.Column("value", sa.JSON(), nullable=False))


def downgrade():
    for name in ["preferences", "login_sessions", "jobs", "subscriptions", "nodes", "accounts"]:
        op.drop_table(name)
