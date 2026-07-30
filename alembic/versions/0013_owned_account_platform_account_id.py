"""owned account platform_account_id

Adds `platform_account_id` (nullable string) to `owned_accounts`: the
platform's own numeric/opaque ID for the account, needed by node-based
platform APIs (e.g. Instagram Graph API's IG Business Account ID) where
the access token alone isn't enough to address the account - "me" doesn't
resolve to it. Purely additive; every existing row simply has NULL.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-28 05:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, Sequence[str], None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('owned_accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('platform_account_id', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('owned_accounts', schema=None) as batch_op:
        batch_op.drop_column('platform_account_id')
