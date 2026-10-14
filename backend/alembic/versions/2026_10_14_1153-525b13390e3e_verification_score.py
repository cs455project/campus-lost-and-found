"""verification score (SCRUM-51, FR-SCORE-01..04)

C = 50M + 20R + 30H stored on each claim (staff only), plus
users.verified_returns for H. Existing users get their count backfilled
from found items they reported that are already RETURNED (staff excluded,
since walk-ins are reported by the guard). Existing claims keep a NULL
score until `python -m app.cli rescore-claims` is run.

Revision ID: 525b13390e3e
Revises: e559c3518d9e
Create Date: 2026-10-14 11:53:51.123175

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '525b13390e3e'
down_revision: Union[str, Sequence[str], None] = 'e559c3518d9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('claims', sa.Column('score', sa.Float(), nullable=True))
    op.add_column('claims', sa.Column('score_m', sa.Float(), nullable=True))
    op.add_column('claims', sa.Column('score_r', sa.Float(), nullable=True))
    op.add_column('claims', sa.Column('score_h', sa.Float(), nullable=True))
    op.add_column('claims', sa.Column('scored_at', sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f('ck_claims_score_range'), 'claims', 'score IS NULL OR (score >= 0 AND score <= 100)'
    )
    op.create_check_constraint(
        op.f('ck_claims_score_factors_range'), 'claims',
        '(score_m IS NULL OR score_m BETWEEN 0 AND 1) AND (score_r IS NULL OR score_r BETWEEN 0 AND 1) '
        'AND (score_h IS NULL OR score_h BETWEEN 0 AND 1)',
    )
    op.add_column('users', sa.Column('verified_returns', sa.Integer(), server_default='0', nullable=False))
    op.create_check_constraint(
        op.f('ck_users_verified_returns_not_negative'), 'users', 'verified_returns >= 0'
    )
    op.execute("""
        UPDATE users SET verified_returns = counts.n
        FROM (SELECT reporter_id, count(*) AS n FROM items
              WHERE kind = 'FOUND' AND status = 'RETURNED' GROUP BY reporter_id) AS counts
        WHERE users.id = counts.reporter_id AND users.role = 'STUDENT'
    """)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f('ck_users_verified_returns_not_negative'), 'users', type_='check')
    op.drop_column('users', 'verified_returns')
    op.drop_constraint(op.f('ck_claims_score_factors_range'), 'claims', type_='check')
    op.drop_constraint(op.f('ck_claims_score_range'), 'claims', type_='check')
    op.drop_column('claims', 'scored_at')
    op.drop_column('claims', 'score_h')
    op.drop_column('claims', 'score_r')
    op.drop_column('claims', 'score_m')
    op.drop_column('claims', 'score')
