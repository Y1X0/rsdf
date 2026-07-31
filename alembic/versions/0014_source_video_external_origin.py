"""source video external origin (content_sources connector, milestone 1)

Adds `source_videos.source` (enum string, default 'upload') and
`source_videos.external_source_id` (nullable string) plus a unique
constraint on the pair. Purely additive and provenance/dedup-only:
transcribe/analyze/render all read only `storage_path` and never look at
either new column, so every existing row and every existing pipeline
behavior is unaffected - every current row simply gets `source='upload'`,
`external_source_id=NULL`. The unique constraint permits any number of
NULL `external_source_id` values (upload rows), which is exactly what's
wanted - only ('content_rewards', <real external id>) pairs are ever
required to be unique.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-30 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, Sequence[str], None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('source_videos', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('source', sa.String(length=32), nullable=False, server_default='upload')
        )
        batch_op.add_column(sa.Column('external_source_id', sa.String(length=200), nullable=True))
        batch_op.create_unique_constraint(
            'uq_source_videos_source_external_id', ['source', 'external_source_id']
        )


def downgrade() -> None:
    with op.batch_alter_table('source_videos', schema=None) as batch_op:
        batch_op.drop_constraint('uq_source_videos_source_external_id', type_='unique')
        batch_op.drop_column('external_source_id')
        batch_op.drop_column('source')
