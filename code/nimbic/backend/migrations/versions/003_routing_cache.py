"""003_routing_cache

Revision ID: 003_routing_cache
Revises: 002_security
Create Date: 2026-06-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

# revision identifiers, used by Alembic.
revision = '003_routing_cache'
down_revision = '002_security'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Enable pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 2. Create custom native PostgreSQL ENUM types
    op.execute("CREATE TYPE cost_savings_source_enum AS ENUM ('cache_hit', 'model_routing', 'faq_hit')")

    # 3. Create 'prompt_embeddings' table
    op.create_table(
        'prompt_embeddings',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('prompt_hash', sa.String(length=64), nullable=False),
        sa.Column('embedding', Vector(384), nullable=False),
        sa.Column('response_text', sa.Text(), nullable=False),
        sa.Column('model_used', sa.String(length=100), nullable=False),
        sa.Column('hit_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('last_hit_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_prompt_embeddings_org_id'), 'prompt_embeddings', ['org_id'], unique=False)
    op.create_index(op.f('ix_prompt_embeddings_prompt_hash'), 'prompt_embeddings', ['prompt_hash'], unique=False)

    # Create ivfflat index on embeddings column using raw SQL
    op.execute("CREATE INDEX ix_prompt_embeddings_embedding ON prompt_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists=100);")

    # 4. Create 'org_faq_cache' table
    op.create_table(
        'org_faq_cache',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(384), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=True),
        sa.Column('hit_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_org_faq_cache_org_id'), 'org_faq_cache', ['org_id'], unique=False)

    # 5. Create 'routing_rules' table
    op.create_table(
        'routing_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('conditions', sa.JSON(), server_default='{}', nullable=False),
        sa.Column('target_model', sa.String(length=100), nullable=False),
        sa.Column('target_provider', sa.String(length=100), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_routing_rules_org_id'), 'routing_rules', ['org_id'], unique=False)

    # 6. Create 'cost_savings_log' table
    op.create_table(
        'cost_savings_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_id', sa.String(length=255), nullable=False),
        sa.Column('actual_model', sa.String(length=100), nullable=False),
        sa.Column('actual_cost_usd', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('baseline_model', sa.String(length=100), server_default='gpt-4o', nullable=False),
        sa.Column('baseline_cost_usd', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('savings_usd', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('source', postgresql.ENUM('cache_hit', 'model_routing', 'faq_hit', name='cost_savings_source_enum', create_type=False), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cost_savings_log_org_id'), 'cost_savings_log', ['org_id'], unique=False)
    op.create_index(op.f('ix_cost_savings_log_request_id'), 'cost_savings_log', ['request_id'], unique=True)


def downgrade() -> None:
    # Drop tables
    op.drop_table('cost_savings_log')
    op.drop_table('routing_rules')
    op.drop_table('org_faq_cache')
    op.drop_table('prompt_embeddings')

    # Drop custom native enum types
    op.execute("DROP TYPE cost_savings_source_enum")

    # Drop pgvector extension if safe (optional, we keep it enabled as default template setup)
    # op.execute("DROP EXTENSION IF EXISTS vector;")
