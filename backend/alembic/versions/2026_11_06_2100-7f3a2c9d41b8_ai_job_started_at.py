"""ai_jobs.started_at: re-run AI jobs interrupted by a stopped worker

Revision ID: 7f3a2c9d41b8
Revises: e56f17155c1c
Create Date: 2026-11-06 21:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7f3a2c9d41b8'
down_revision: Union[str, Sequence[str], None] = 'e56f17155c1c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('ai_jobs', sa.Column('started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('ai_jobs', 'started_at')
