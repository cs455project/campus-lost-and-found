"""category custody required (SCRUM-17, BR-02)

Found items in a custody_required category must always go to the SIS Guard.
Bicycles ("cycle") are switched on here; seed.py does the same for new databases.

Revision ID: 0f3606302be1
Revises: 2c9f12cd6dff
Create Date: 2026-10-14 11:46:07.152105

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f3606302be1'
down_revision: Union[str, Sequence[str], None] = '2c9f12cd6dff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('categories', sa.Column('custody_required', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.execute("UPDATE categories SET custody_required = true WHERE slug = 'cycle'")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('categories', 'custody_required')
