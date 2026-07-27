"""word-level transcript timestamps for tightly-synced clip captions

Adds `source_videos.transcript_words` (nullable JSON, same shape/treatment
as the existing `transcript_segments`): word-level timing from
GroqWhisperProvider, used by FfmpegClipRenderer to burn captions in close
sync with the exact moment each word is spoken rather than showing a
whole multi-second segment's text all at once. Purely additive - every
existing source_videos row simply has NULL here (falls back to the
existing segment-level captioning behavior, unchanged).

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-27 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('source_videos', schema=None) as batch_op:
        batch_op.add_column(sa.Column('transcript_words', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('source_videos', schema=None) as batch_op:
        batch_op.drop_column('transcript_words')
