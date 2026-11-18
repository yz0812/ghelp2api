"""Track managed converter publications without modifying existing URLs."""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"


def upgrade():
    op.create_table(
        "conversions",
        sa.Column("id", sa.String(32), sa.ForeignKey("subscriptions.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("source_id", sa.String(32), sa.ForeignKey("sources.id"), unique=True, nullable=False),
        sa.Column("template", sa.String(16), nullable=False),
        sa.Column("owns_source", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Float()),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.Column("request_key", sa.String(64), unique=True),
        sa.Column("request_hash", sa.String(64)),
    )
    op.create_index("ix_conversions_updated_at", "conversions", ["updated_at"])


def downgrade():
    op.drop_table("conversions")
