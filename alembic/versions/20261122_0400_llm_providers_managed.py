"""llm_providers: owner-managed LLM endpoints + switch all shops to managed

Revision ID: c1a2b3d4e5f6
Revises: 5fdc27d91050
Create Date: 2026-11-22 04:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = 'c1a2b3d4e5f6'
down_revision: str | Sequence[str] | None = '5fdc27d91050'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'llm_providers',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('label', sa.String(length=80), nullable=False),
        sa.Column('base_url', sa.String(length=300), nullable=False),
        sa.Column('api_key_encrypted', sa.Text(), nullable=False, server_default=''),
        sa.Column('models', sa.JSON(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    # BYOK is replaced by a server-side model; keep old encrypted keys for rollback.
    op.execute("UPDATE shops SET ai_provider = 'managed', ai_model = 'managed' WHERE ai_provider IS DISTINCT FROM 'managed'")


def downgrade() -> None:
    op.execute("UPDATE shops SET ai_provider = 'openai', ai_model = '' WHERE ai_provider = 'managed'")
    op.drop_table('llm_providers')
