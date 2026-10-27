"""订阅的延迟上限与定时测速记录。"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"


def upgrade():
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(sa.Column("max_latency", sa.Integer()))
        batch.add_column(sa.Column("last_probe", sa.Float()))


def downgrade():
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("last_probe")
        batch.drop_column("max_latency")
