"""fix source_videos.source enum value case mismatch

Real production bug, confirmed via a real Render traceback on
GET /source-videos:

    LookupError: 'upload' is not among the defined enum values.
    Enum name: sourcevideoorigin. Possible values: UPLOAD, CONTENT_REWARDS.

Root cause: migration 0014 added `source_videos.source` with
`server_default='upload'` (lowercase - matching SourceVideoOrigin.UPLOAD's
*value*). SQLAlchemy's Enum column type (native_enum=False, no
values_callable) stores and reads using the enum member's *name*
("UPLOAD"/"CONTENT_REWARDS"), not its value, by default - so the raw SQL
server_default backfilled every row that already existed at migration
0014's time with the literal string 'upload' (lowercase), which the ORM
cannot map back to any SourceVideoOrigin member on read. Any row created
through the ORM after 0014 (the normal sync-content-rewards / manual
upload code paths, both of which assign SourceVideoOrigin.UPLOAD /
.CONTENT_REWARDS as real enum members) is unaffected - only rows
backfilled by that one server_default carry the broken lowercase value.

This is a pure data fix: no column type, constraint, or application code
changes. Confirmed via a repo-wide grep that this is the only
`server_default=` in any migration, so this is an isolated, one-off
issue, not a systemic pattern.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-01 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, Sequence[str], None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


source_videos = sa.table('source_videos', sa.column('source', sa.String))


def upgrade() -> None:
    op.execute(
        source_videos.update().where(source_videos.c.source == 'upload').values(source='UPLOAD')
    )
    op.execute(
        source_videos.update()
        .where(source_videos.c.source == 'content_rewards')
        .values(source='CONTENT_REWARDS')
    )


def downgrade() -> None:
    op.execute(
        source_videos.update().where(source_videos.c.source == 'UPLOAD').values(source='upload')
    )
    op.execute(
        source_videos.update()
        .where(source_videos.c.source == 'CONTENT_REWARDS')
        .values(source='content_rewards')
    )
