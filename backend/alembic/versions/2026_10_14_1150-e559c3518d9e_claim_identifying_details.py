"""claim identifying details (SCRUM-41, FR-CLM-02)

Each claim can list up to 5 identifying details; a guard marks each one
MATCH or NO_MATCH against the item. Private to the claimant and staff.

Revision ID: e559c3518d9e
Revises: 0f3606302be1
Create Date: 2026-10-14 11:50:53.058866

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e559c3518d9e'
down_revision: Union[str, Sequence[str], None] = '0f3606302be1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('claim_details',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('claim_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=8), nullable=False),
    sa.Column('text', sa.String(length=200), nullable=False),
    sa.Column('verdict', sa.String(length=8), nullable=True),
    sa.Column('checked_by_id', sa.Integer(), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source IN ('CLAIMANT', 'DESK')", name=op.f('ck_claim_details_claim_detail_source')),
    sa.CheckConstraint("verdict IN ('MATCH', 'NO_MATCH')", name=op.f('ck_claim_details_claim_detail_verdict')),
    sa.ForeignKeyConstraint(['checked_by_id'], ['users.id'], name=op.f('fk_claim_details_checked_by_id_users'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['claim_id'], ['claims.id'], name=op.f('fk_claim_details_claim_id_claims'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_claim_details'))
    )
    op.create_index(op.f('ix_claim_details_claim_id'), 'claim_details', ['claim_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_claim_details_claim_id'), table_name='claim_details')
    op.drop_table('claim_details')
