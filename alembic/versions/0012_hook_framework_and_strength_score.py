"""hook framework taxonomy + pre-publish hook strength score

Adds `hook_framework` (nullable string) and `hook_strength_score`
(nullable float) to both `scripts` and `clips`: which named pattern from
services/hook_scoring.py's HOOK_FRAMEWORKS the LLM says it used, and a
pre-publish heuristic score (0-100) from score_hook_strength(). Purely
additive - every existing row simply has NULL here (both agents already
worked fine without these values; this is a new capability layered on
top, not a required field).

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-27 20:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, Sequence[str], None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('scripts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hook_framework', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('hook_strength_score', sa.Float(), nullable=True))

    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.add_column(sa.Column('hook_framework', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('hook_strength_score', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('clips', schema=None) as batch_op:
        batch_op.drop_column('hook_strength_score')
        batch_op.drop_column('hook_framework')

    with op.batch_alter_table('scripts', schema=None) as batch_op:
        batch_op.drop_column('hook_strength_score')
        batch_op.drop_column('hook_framework')
