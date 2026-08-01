"""clip quality scores

Adds a new `clip_quality_scores` table, deliberately separate from the
existing `quality_scores` table (which is shaped entirely for the Script
pipeline's AI-generated text - originality/policy-risk - and has no room
for a Clip's own real signals). Only hook_strength_score/
caption_coverage_score/scene_alignment_score are computed from real data
today; retention_prediction_score/cta_quality_score/speech_clarity_score
are nullable placeholders for a later phase, same "null = not yet
available" convention as quality_scores' own retention_prediction_score.

Purely additive - no existing table is touched.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-01 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, Sequence[str], None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'clip_quality_scores',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.Integer(), nullable=False),
        sa.Column('hook_strength_score', sa.Float(), nullable=True),
        sa.Column('caption_coverage_score', sa.Float(), nullable=True),
        sa.Column('scene_alignment_score', sa.Float(), nullable=True),
        sa.Column('retention_prediction_score', sa.Float(), nullable=True),
        sa.Column('cta_quality_score', sa.Float(), nullable=True),
        sa.Column('speech_clarity_score', sa.Float(), nullable=True),
        sa.Column('model_version', sa.String(length=50), nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['video_id'], ['videos.id'], ),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('clip_quality_scores', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_clip_quality_scores_video_id'), ['video_id'], unique=True
        )


def downgrade() -> None:
    with op.batch_alter_table('clip_quality_scores', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_clip_quality_scores_video_id'))
    op.drop_table('clip_quality_scores')
