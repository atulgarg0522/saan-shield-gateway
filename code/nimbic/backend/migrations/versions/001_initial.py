"""001_initial

Revision ID: 001_initial
Revises: 
Create Date: 2026-06-01 13:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create custom native PostgreSQL ENUM types
    op.execute("CREATE TYPE organization_plan_enum AS ENUM ('free', 'starter', 'pro', 'enterprise')")
    op.execute("CREATE TYPE provider_enum AS ENUM ('openai', 'anthropic', 'gemini', 'azure_openai', 'bedrock')")

    # 2. Create 'organizations' table
    op.create_table(
        'organizations',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('plan', postgresql.ENUM('free', 'starter', 'pro', 'enterprise', name='organization_plan_enum', create_type=False), server_default='free', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_organizations_slug'), 'organizations', ['slug'], unique=True)

    # 3. Create 'api_keys' table
    op.create_table(
        'api_keys',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('key_hash', sa.String(length=255), nullable=False),
        sa.Column('key_prefix', sa.String(length=8), nullable=False),
        sa.Column('scopes', sa.JSON(), server_default='[]', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_api_keys_key_hash'), 'api_keys', ['key_hash'], unique=True)
    op.create_index(op.f('ix_api_keys_org_id'), 'api_keys', ['org_id'], unique=False)

    # 4. Create 'provider_configs' table
    op.create_table(
        'provider_configs',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('provider', postgresql.ENUM('openai', 'anthropic', 'gemini', 'azure_openai', 'bedrock', name='provider_enum', create_type=False), nullable=False),
        sa.Column('api_key_encrypted', sa.String(length=512), nullable=False),
        sa.Column('base_url', sa.String(length=512), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_provider_configs_org_id'), 'provider_configs', ['org_id'], unique=False)
    op.create_index(op.f('ix_provider_configs_provider'), 'provider_configs', ['provider'], unique=False)

    # 5. Create 'requests_log' table
    op.create_table(
        'requests_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('api_key_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('request_id', sa.String(length=255), nullable=False),
        sa.Column('provider', postgresql.ENUM('openai', 'anthropic', 'gemini', 'azure_openai', 'bedrock', name='provider_enum', create_type=False), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('completion_tokens', sa.Integer(), server_default='0', nullable=False),
        sa.Column('total_tokens', sa.Integer(), server_default='0', nullable=False),
        # Numeric(10, 6) ensures exact cost mapping up to millionths of a dollar (e.g. $0.000002)
        sa.Column('cost_usd', sa.Numeric(precision=10, scale=6), nullable=False),
        sa.Column('latency_ms', sa.Integer(), nullable=False),
        sa.Column('status_code', sa.Integer(), nullable=False),
        sa.Column('error_message', sa.String(), nullable=True),
        sa.Column('user_identifier', sa.String(length=255), nullable=True),
        sa.Column('request_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['api_key_id'], ['api_keys.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_requests_log_api_key_id'), 'requests_log', ['api_key_id'], unique=False)
    op.create_index(op.f('ix_requests_log_org_id'), 'requests_log', ['org_id'], unique=False)
    op.create_index(op.f('ix_requests_log_provider'), 'requests_log', ['provider'], unique=False)
    op.create_index(op.f('ix_requests_log_model'), 'requests_log', ['model'], unique=False)
    op.create_index(op.f('ix_requests_log_request_id'), 'requests_log', ['request_id'], unique=True)
    op.create_index(op.f('ix_requests_log_status_code'), 'requests_log', ['status_code'], unique=False)
    op.create_index(op.f('ix_requests_log_user_identifier'), 'requests_log', ['user_identifier'], unique=False)


def downgrade() -> None:
    # 1. Drop tables in reverse order of creation
    op.drop_table('requests_log')
    op.drop_table('provider_configs')
    op.drop_table('api_keys')
    op.drop_table('organizations')

    # 2. Drop custom native enum types
    op.execute("DROP TYPE provider_enum")
    op.execute("DROP TYPE organization_plan_enum")
