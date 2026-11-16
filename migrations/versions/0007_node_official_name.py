"""保存通过 PAC 测速域名精确关联的官方线路名称。"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"


def upgrade():
    with op.batch_alter_table("nodes") as batch:
        batch.add_column(sa.Column("official_name", sa.String(160), nullable=True))


def downgrade():
    with op.batch_alter_table("nodes") as batch:
        batch.drop_column("official_name")
