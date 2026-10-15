"""remove secret detail

The secret identifying detail is no longer used: claims are checked against
the item itself. Drops both columns and their CHECK rules. Any secret details
already stored are deleted (downgrade brings the empty columns back).

Revision ID: 31039bac1bb2
Revises: 329dfd45c1e3
Create Date: 2026-10-13 15:53:24.215104

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '31039bac1bb2'
down_revision: Union[str, Sequence[str], None] = '329dfd45c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(op.f("ck_items_secret_only_for_found"), "items", type_="check")
    op.drop_constraint(op.f("ck_items_secret_source"), "items", type_="check")
    op.drop_column("items", "secret_detail_by")
    op.drop_column("items", "secret_detail")


def downgrade() -> None:
    op.add_column("items", sa.Column("secret_detail", sa.String(length=300), nullable=True))
    op.add_column("items", sa.Column("secret_detail_by", sa.String(length=6), nullable=True))
    op.create_check_constraint(
        op.f("ck_items_secret_source"), "items", "secret_detail_by IN ('FINDER', 'GUARD')"
    )
    op.create_check_constraint(
        op.f("ck_items_secret_only_for_found"), "items", "kind = 'FOUND' OR secret_detail IS NULL"
    )
