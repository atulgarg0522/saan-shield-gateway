"""005_shadow_mode

Revision ID: 005_shadow_mode
Revises: 004_ab_testing
Create Date: 2026-06-05 11:10:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005_shadow_mode'
down_revision = '004_ab_testing'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add test_mode to ab_tests
    op.add_column(
        'ab_tests',
        sa.Column('test_mode', sa.String(length=50), server_default='traffic_split', nullable=False)
    )

    # 2. Create 'shadow_results' table
    op.create_table(
        'shadow_results',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('prompt_hash', sa.String(length=64), nullable=False),
        sa.Column('model_a_cost', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('model_b_cost', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('model_a_latency', sa.Integer(), nullable=False),
        sa.Column('model_b_latency', sa.Integer(), nullable=False),
        sa.Column('model_a_tokens', sa.Integer(), nullable=False),
        sa.Column('model_b_tokens', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['test_id'], ['ab_tests.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_shadow_results_test_id'), 'shadow_results', ['test_id'], unique=False)


def downgrade() -> None:
    op.drop_table('shadow_results')
    op.drop_column('ab_tests', 'test_mode')
