"""link found item to lost report

A found item reported with "I found this" remembers which lost report it
answers (items.answers_lost_id). Its details are then locked for the finder.

Revision ID: 2c9f12cd6dff
Revises: 31039bac1bb2
Create Date: 2026-10-13 18:48:58.393168

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c9f12cd6dff'
down_revision: Union[str, Sequence[str], None] = '31039bac1bb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('items', sa.Column('answers_lost_id', sa.Integer(), nullable=True))
    op.create_foreign_key(op.f('fk_items_answers_lost_id_items'), 'items', 'items', ['answers_lost_id'], ['id'], ondelete='SET NULL')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('fk_items_answers_lost_id_items'), 'items', type_='foreignkey')
    op.drop_column('items', 'answers_lost_id')
