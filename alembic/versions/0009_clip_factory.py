"""clip factory: source videos and clips (long-form -> short-clip montage)

Adds the two new tables behind the "montage" pipeline: `source_videos`
(a long-form video the operator uploads, plus its transcript) and `clips`
(AI-suggested highlight moments from that transcript, each optionally
rendered into a real Video). `videos.script_id` is loosened to nullable
and a new nullable `videos.clip_id` is added, since a Video can now
originate from either the existing Script pipeline or this new Clip
pipeline — exactly one of the two, enforced in the service layer, not a
DB constraint (matching every other cross-entity invariant in this
codebase). Purely additive: no existing column is dropped or retyped,
and every existing Script-originated Video row keeps its script_id
exactly as before.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26 22:05:02.207660

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('source_videos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('campaign_id', sa.Integer(), nullable=True),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('storage_path', sa.String(length=500), nullable=False),
    sa.Column('duration_s', sa.Float(), nullable=True),
    sa.Column('transcription_status', sa.Enum('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED', name='processingstatus', native_enum=False, length=32), nullable=False),
    sa.Column('transcript_text', sa.Text(), nullable=True),
    sa.Column('transcript_segments', sa.JSON(), nullable=True),
    sa.Column('transcription_agent_run_id', sa.Integer(), nullable=True),
    sa.Column('analysis_status', sa.Enum('PENDING', 'IN_PROGRESS', 'COMPLETED', 'FAILED', name='processingstatus', native_enum=False, length=32), nullable=False),
    sa.Column('analysis_agent_run_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['analysis_agent_run_id'], ['agent_runs.id'], ),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ),
    sa.ForeignKeyConstraint(['transcription_agent_run_id'], ['agent_runs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_source_videos_campaign_id'), 'source_videos', ['campaign_id'], unique=False)

    op.create_table('clips',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_video_id', sa.Integer(), nullable=False),
    sa.Column('start_s', sa.Float(), nullable=False),
    sa.Column('end_s', sa.Float(), nullable=False),
    sa.Column('hook_text', sa.Text(), nullable=True),
    sa.Column('caption_text', sa.Text(), nullable=True),
    sa.Column('predicted_score', sa.Float(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('SUGGESTED', 'RENDERED', 'REJECTED', name='clipstatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_video_id'], ['source_videos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_clips_source_video_id'), 'clips', ['source_video_id'], unique=False)
    op.create_index(op.f('ix_clips_status'), 'clips', ['status'], unique=False)

    op.add_column('videos', sa.Column('clip_id', sa.Integer(), nullable=True))
    op.alter_column('videos', 'script_id', existing_type=sa.INTEGER(), nullable=True)
    op.create_index(op.f('ix_videos_clip_id'), 'videos', ['clip_id'], unique=False)
    # Named explicitly (autogenerate's default anonymous name can't be
    # dropped by name in downgrade() without a naming_convention configured
    # on Base.metadata, which this project doesn't set) - see downgrade().
    op.create_foreign_key('fk_videos_clip_id_clips', 'videos', 'clips', ['clip_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_videos_clip_id_clips', 'videos', type_='foreignkey')
    op.drop_index(op.f('ix_videos_clip_id'), table_name='videos')
    op.alter_column('videos', 'script_id', existing_type=sa.INTEGER(), nullable=False)
    op.drop_column('videos', 'clip_id')

    op.drop_index(op.f('ix_clips_status'), table_name='clips')
    op.drop_index(op.f('ix_clips_source_video_id'), table_name='clips')
    op.drop_table('clips')

    op.drop_index(op.f('ix_source_videos_campaign_id'), table_name='source_videos')
    op.drop_table('source_videos')
