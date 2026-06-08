"""004_ab_testing

Revision ID: 004_ab_testing
Revises: 003_routing_cache
Create Date: 2026-06-03 12:47:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '004_ab_testing'
down_revision = '003_routing_cache'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create 'ab_tests' table
    op.create_table(
        'ab_tests',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('model_a', sa.String(length=100), nullable=False),
        sa.Column('provider_a', sa.String(length=100), nullable=False),
        sa.Column('model_b', sa.String(length=100), nullable=False),
        sa.Column('provider_b', sa.String(length=100), nullable=False),
        sa.Column('split_pct', sa.Integer(), server_default='20', nullable=False),
        sa.Column('status', sa.String(length=50), server_default='active', nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('results', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ab_tests_org_id'), 'ab_tests', ['org_id'], unique=False)

    # 2. Create 'ab_test_results' table
    op.create_table(
        'ab_test_results',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('test_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_id', sa.String(length=255), nullable=False),
        sa.Column('variant', sa.String(length=10), nullable=False),
        sa.Column('cost', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('latency', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['test_id'], ['ab_tests.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ab_test_results_test_id'), 'ab_test_results', ['test_id'], unique=False)
    op.create_index(op.f('ix_ab_test_results_org_id'), 'ab_test_results', ['org_id'], unique=False)


def downgrade() -> None:
    op.drop_table('ab_test_results')
    op.drop_table('ab_tests')
