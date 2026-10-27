"""订阅源记录最近一次尝试时间，用于失败退避。"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"


def upgrade():
    op.add_column("sources", sa.Column("last_attempt", sa.Float()))


def downgrade():
    with op.batch_alter_table("sources") as batch:
        batch.drop_column("last_attempt")
