"""002_security

Revision ID: 002_security
Revises: 001_initial
Create Date: 2026-06-02 10:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '002_security'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create custom native PostgreSQL ENUM types
    op.execute("CREATE TYPE violation_type_enum AS ENUM ('pii', 'source_code', 'sensitive_content', 'data_residency')")
    op.execute("CREATE TYPE severity_enum AS ENUM ('low', 'medium', 'high', 'critical')")
    op.execute("CREATE TYPE action_taken_enum AS ENUM ('allowed', 'redacted', 'warned', 'blocked')")
    op.execute("CREATE TYPE pii_action_enum AS ENUM ('allow', 'redact', 'warn', 'block')")
    op.execute("CREATE TYPE policy_action_enum AS ENUM ('allow', 'warn', 'block')")

    # 2. Create 'security_policies' table
    op.create_table(
        'security_policies',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('pii_action', postgresql.ENUM('allow', 'redact', 'warn', 'block', name='pii_action_enum', create_type=False), server_default='redact', nullable=False),
        sa.Column('code_action', postgresql.ENUM('allow', 'warn', 'block', name='policy_action_enum', create_type=False), server_default='warn', nullable=False),
        sa.Column('sensitive_action', postgresql.ENUM('allow', 'warn', 'block', name='policy_action_enum', create_type=False), server_default='warn', nullable=False),
        sa.Column('blocked_regions', sa.JSON(), server_default='[]', nullable=False),
        sa.Column('allowed_providers_by_region', sa.JSON(), server_default='{}', nullable=False),
        sa.Column('custom_patterns', sa.JSON(), server_default='[]', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id')
    )
    op.create_index(op.f('ix_security_policies_org_id'), 'security_policies', ['org_id'], unique=True)

    # 3. Create 'security_violations' table
    op.create_table(
        'security_violations',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('request_id', sa.String(length=255), nullable=False),
        sa.Column('violation_type', postgresql.ENUM('pii', 'source_code', 'sensitive_content', 'data_residency', name='violation_type_enum', create_type=False), nullable=False),
        sa.Column('severity', postgresql.ENUM('low', 'medium', 'high', 'critical', name='severity_enum', create_type=False), nullable=False),
        sa.Column('action_taken', postgresql.ENUM('allowed', 'redacted', 'warned', 'blocked', name='action_taken_enum', create_type=False), nullable=False),
        sa.Column('details', sa.JSON(), server_default='{}', nullable=False),
        sa.Column('prompt_snippet', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_security_violations_org_id'), 'security_violations', ['org_id'], unique=False)
    op.create_index(op.f('ix_security_violations_request_id'), 'security_violations', ['request_id'], unique=False)
    op.create_index(op.f('ix_security_violations_violation_type'), 'security_violations', ['violation_type'], unique=False)
    op.create_index(op.f('ix_security_violations_severity'), 'security_violations', ['severity'], unique=False)
    op.create_index(op.f('ix_security_violations_action_taken'), 'security_violations', ['action_taken'], unique=False)


def downgrade() -> None:
    # Drop tables
    op.drop_table('security_violations')
    op.drop_table('security_policies')

    # Drop custom native enum types
    op.execute("DROP TYPE policy_action_enum")
    op.execute("DROP TYPE pii_action_enum")
    op.execute("DROP TYPE action_taken_enum")
    op.execute("DROP TYPE severity_enum")
    op.execute("DROP TYPE violation_type_enum")
