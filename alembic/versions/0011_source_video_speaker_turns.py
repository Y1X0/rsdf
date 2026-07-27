"""speaker diarization turns for per-speaker clip captioning

Adds `source_videos.speaker_turns` (nullable JSON, same shape/treatment as
the existing `transcript_words`): who is talking during each stretch of
the source video, from a real (optional, heavy, off-by-default) speaker
diarization provider - see diarization/base.py. Purely additive - every
existing source_videos row simply has NULL here (falls back to the
existing single-speaker-assumed captioning behavior, unchanged).

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-27 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, Sequence[str], None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('source_videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('speaker_turns', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('source_videos', schema=None) as batch_op:
        batch_op.drop_column('speaker_turns')
