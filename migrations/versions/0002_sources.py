"""订阅源与节点探测结果。"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"


def upgrade():
    op.create_table("sources",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("url_cipher", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("proxies", sa.JSON(), nullable=False),
        sa.Column("last_fetch", sa.Float()), sa.Column("last_error", sa.Text()),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float(), nullable=False))
    op.create_table("proxy_checks",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("latency", sa.Float()), sa.Column("method", sa.String(10), nullable=False),
        sa.Column("tested_at", sa.Float(), nullable=False))
    op.add_column("subscriptions", sa.Column("source_ids", sa.JSON(), nullable=False,
                                             server_default=sa.text("'[]'")))
    op.add_column("subscriptions", sa.Column("dedupe", sa.Boolean(), nullable=False,
                                             server_default=sa.text("1")))


def downgrade():
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("dedupe")
        batch.drop_column("source_ids")
    op.drop_table("proxy_checks")
    op.drop_table("sources")
