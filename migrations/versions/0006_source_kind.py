"""订阅源区分远程地址与本地导入的文件。"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"


def upgrade():
    with op.batch_alter_table("sources") as batch:
        batch.add_column(sa.Column("kind", sa.String(10), nullable=False, server_default="url"))


def downgrade():
    with op.batch_alter_table("sources") as batch:
        batch.drop_column("kind")
