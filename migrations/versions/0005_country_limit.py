"""订阅的每国节点数量上限。"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"


def upgrade():
    with op.batch_alter_table("subscriptions") as batch:
        batch.add_column(sa.Column("max_per_country", sa.Integer()))


def downgrade():
    with op.batch_alter_table("subscriptions") as batch:
        batch.drop_column("max_per_country")
