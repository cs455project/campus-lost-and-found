"""add ai jobs and match suggestions

AI Phase A: the ai_jobs queue, match_suggestions (top-K similar items,
stored in both directions), and per-item AI columns (status, embedding).

Revision ID: c0afb0590d13
Revises: 2c9f12cd6dff
Create Date: 2026-10-14 13:22:30.474889

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c0afb0590d13'
down_revision: Union[str, Sequence[str], None] = '2c9f12cd6dff'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('ai_jobs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=20), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.String(length=500), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("kind IN ('ITEM_ANALYSE')", name=op.f('ck_ai_jobs_kind')),
    sa.CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name=op.f('ck_ai_jobs_status')),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_ai_jobs_item_id_items'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_jobs'))
    )
    op.create_index(op.f('ix_ai_jobs_item_id'), 'ai_jobs', ['item_id'], unique=False)
    op.create_index('ix_ai_jobs_queued', 'ai_jobs', ['id'], unique=False, postgresql_where=sa.text("status = 'queued'"))
    op.create_index('uq_ai_jobs_one_queued', 'ai_jobs', ['kind', 'item_id'], unique=True, postgresql_where=sa.text("status = 'queued'"))
    op.create_table('match_suggestions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('candidate_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Float(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('method', sa.String(length=10), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('item_id <> candidate_id', name=op.f('ck_match_suggestions_not_self')),
    sa.CheckConstraint('score >= 0 AND score <= 1', name=op.f('ck_match_suggestions_score_range')),
    sa.ForeignKeyConstraint(['candidate_id'], ['items.id'], name=op.f('fk_match_suggestions_candidate_id_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_match_suggestions_item_id_items'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_suggestions'))
    )
    op.create_index(op.f('ix_match_suggestions_candidate_id'), 'match_suggestions', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_match_suggestions_item_id'), 'match_suggestions', ['item_id'], unique=False)
    op.create_index('uq_match_suggestions_pair', 'match_suggestions', ['item_id', 'candidate_id'], unique=True)
    op.add_column('items', sa.Column('ai_status', sa.String(length=10), nullable=True))
    op.add_column('items', sa.Column('ai_updated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('items', sa.Column('embedding', postgresql.ARRAY(sa.REAL()), nullable=True))
    op.add_column('items', sa.Column('embedding_model', sa.String(length=80), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('items', 'embedding_model')
    op.drop_column('items', 'embedding')
    op.drop_column('items', 'ai_updated_at')
    op.drop_column('items', 'ai_status')
    op.drop_index('uq_match_suggestions_pair', table_name='match_suggestions')
    op.drop_index(op.f('ix_match_suggestions_item_id'), table_name='match_suggestions')
    op.drop_index(op.f('ix_match_suggestions_candidate_id'), table_name='match_suggestions')
    op.drop_table('match_suggestions')
    op.drop_index('uq_ai_jobs_one_queued', table_name='ai_jobs', postgresql_where=sa.text("status = 'queued'"))
    op.drop_index('ix_ai_jobs_queued', table_name='ai_jobs', postgresql_where=sa.text("status = 'queued'"))
    op.drop_index(op.f('ix_ai_jobs_item_id'), table_name='ai_jobs')
    op.drop_table('ai_jobs')
