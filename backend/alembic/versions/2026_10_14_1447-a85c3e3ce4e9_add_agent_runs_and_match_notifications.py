"""add agent runs and match notifications

AI Phase B: agent_runs (the AI audit log: model, tool calls, outcome) and
match_notifications (an owner is emailed about each match only once).

Revision ID: a85c3e3ce4e9
Revises: c0afb0590d13
Create Date: 2026-10-14 14:47:07.197091

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a85c3e3ce4e9'
down_revision: Union[str, Sequence[str], None] = 'c0afb0590d13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('match_notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lost_item_id', sa.Integer(), nullable=False),
    sa.Column('found_item_id', sa.Integer(), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['found_item_id'], ['items.id'], name=op.f('fk_match_notifications_found_item_id_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['lost_item_id'], ['items.id'], name=op.f('fk_match_notifications_lost_item_id_items'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_match_notifications'))
    )
    op.create_index('uq_match_notifications_pair', 'match_notifications', ['lost_item_id', 'found_item_id'], unique=True)
    op.create_table('agent_runs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('outcome', sa.String(length=10), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=True),
    sa.Column('model', sa.String(length=80), nullable=True),
    sa.Column('llm_calls', sa.Integer(), nullable=False),
    sa.Column('steps', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('summary', sa.String(length=1000), nullable=True),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("outcome IN ('ok', 'fallback', 'error')", name=op.f('ck_agent_runs_outcome')),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_agent_runs_item_id_items'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['ai_jobs.id'], name=op.f('fk_agent_runs_job_id_ai_jobs'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_agent_runs'))
    )
    op.create_index(op.f('ix_agent_runs_item_id'), 'agent_runs', ['item_id'], unique=False)
    op.create_index('ix_agent_runs_started_at', 'agent_runs', ['started_at'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_agent_runs_started_at', table_name='agent_runs')
    op.drop_index(op.f('ix_agent_runs_item_id'), table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_index('uq_match_notifications_pair', table_name='match_notifications')
    op.drop_table('match_notifications')
