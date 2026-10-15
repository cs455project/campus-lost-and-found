"""add ai photo descriptions

AI Phase C: items.ai_description / ai_attributes (what the AI saw in the
photos) and ai_photos_key (which photos were described, to avoid repeats).

Revision ID: e41c12d5e1ac
Revises: a85c3e3ce4e9
Create Date: 2026-10-14 16:50:40.777233

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e41c12d5e1ac'
down_revision: Union[str, Sequence[str], None] = 'a85c3e3ce4e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('items', sa.Column('ai_description', sa.Text(), nullable=True))
    op.add_column('items', sa.Column('ai_attributes', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('items', sa.Column('ai_photos_key', sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('items', 'ai_photos_key')
    op.drop_column('items', 'ai_attributes')
    op.drop_column('items', 'ai_description')
