"""008_finops

Revision ID: 008_finops
Revises: 005_shadow_mode
Create Date: 2026-06-05 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '008_finops'
down_revision = '005_shadow_mode'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    if is_postgres:
        op.execute("CREATE TYPE budget_scope_enum AS ENUM ('org', 'team', 'project', 'user')")
        op.execute("CREATE TYPE budget_period_enum AS ENUM ('daily', 'weekly', 'monthly')")
        op.execute("CREATE TYPE budget_alert_type_enum AS ENUM ('soft_warning', 'hard_block')")
        
        scope_type_type = postgresql.ENUM('org', 'team', 'project', 'user', name='budget_scope_enum', create_type=False)
        period_type = postgresql.ENUM('daily', 'weekly', 'monthly', name='budget_period_enum', create_type=False)
        alert_type_type = postgresql.ENUM('soft_warning', 'hard_block', name='budget_alert_type_enum', create_type=False)
        uuid_type = postgresql.UUID(as_uuid=True)
    else:
        scope_type_type = sa.Enum('org', 'team', 'project', 'user', name='budget_scope_enum')
        period_type = sa.Enum('daily', 'weekly', 'monthly', name='budget_period_enum')
        alert_type_type = sa.Enum('soft_warning', 'hard_block', name='budget_alert_type_enum')
        uuid_type = sa.CHAR(32)

    # 1. Create 'teams' table
    op.create_table(
        'teams',
        sa.Column('id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), server_default=sa.text('gen_random_uuid()') if is_postgres else None, nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('department', sa.String(length=255), nullable=True),
        sa.Column('budget_limit_usd', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('budget_alert_pct', sa.Integer(), server_default='80', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_teams_org_id'), 'teams', ['org_id'], unique=False)

    # 2. Create 'projects' table
    op.create_table(
        'projects',
        sa.Column('id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), server_default=sa.text('gen_random_uuid()') if is_postgres else None, nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=False),
        sa.Column('team_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('budget_limit_usd', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('budget_alert_pct', sa.Integer(), server_default='80', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_org_id'), 'projects', ['org_id'], unique=False)
    op.create_index(op.f('ix_projects_team_id'), 'projects', ['team_id'], unique=False)

    # 3. Create 'budgets' table
    op.create_table(
        'budgets',
        sa.Column('id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), server_default=sa.text('gen_random_uuid()') if is_postgres else None, nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=False),
        sa.Column('scope_type', scope_type_type, nullable=False),
        sa.Column('scope_id', uuid_type, nullable=False),
        sa.Column('period', period_type, nullable=False),
        sa.Column('limit_usd', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('alert_pct', sa.Integer(), server_default='80', nullable=False),
        sa.Column('hard_limit', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_budgets_org_id'), 'budgets', ['org_id'], unique=False)
    op.create_index(op.f('ix_budgets_scope_id'), 'budgets', ['scope_id'], unique=False)

    # 4. Create 'budget_alerts' table
    op.create_table(
        'budget_alerts',
        sa.Column('id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), server_default=sa.text('gen_random_uuid()') if is_postgres else None, nullable=False),
        sa.Column('budget_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=False),
        sa.Column('org_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=False),
        sa.Column('alert_type', alert_type_type, nullable=False),
        sa.Column('usage_pct', sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column('usage_usd', sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column('limit_usd', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['budget_id'], ['budgets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_budget_alerts_budget_id'), 'budget_alerts', ['budget_id'], unique=False)
    op.create_index(op.f('ix_budget_alerts_org_id'), 'budget_alerts', ['org_id'], unique=False)

    # 5. Add columns to 'requests_log'
    op.add_column('requests_log', sa.Column('team_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=True))
    op.add_column('requests_log', sa.Column('project_id', postgresql.UUID(as_uuid=True) if is_postgres else sa.CHAR(32), nullable=True))
    op.add_column('requests_log', sa.Column('department', sa.String(length=255), nullable=True))

    op.create_foreign_key('fk_requests_log_team_id', 'requests_log', 'teams', ['team_id'], ['id'], ondelete='SET NULL')
    op.create_foreign_key('fk_requests_log_project_id', 'requests_log', 'projects', ['project_id'], ['id'], ondelete='SET NULL')
    op.create_index(op.f('ix_requests_log_team_id'), 'requests_log', ['team_id'], unique=False)
    op.create_index(op.f('ix_requests_log_project_id'), 'requests_log', ['project_id'], unique=False)
    op.create_index(op.f('ix_requests_log_department'), 'requests_log', ['department'], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == 'postgresql'

    # Remove columns and keys from 'requests_log'
    op.drop_constraint('fk_requests_log_project_id', 'requests_log', type_='foreignkey')
    op.drop_constraint('fk_requests_log_team_id', 'requests_log', type_='foreignkey')
    op.drop_index(op.f('ix_requests_log_department'), table_name='requests_log')
    op.drop_index(op.f('ix_requests_log_project_id'), table_name='requests_log')
    op.drop_index(op.f('ix_requests_log_team_id'), table_name='requests_log')
    op.drop_column('requests_log', 'department')
    op.drop_column('requests_log', 'project_id')
    op.drop_column('requests_log', 'team_id')

    # Drop tables
    op.drop_table('budget_alerts')
    op.drop_table('budgets')
    op.drop_table('projects')
    op.drop_table('teams')

    if is_postgres:
        op.execute("DROP TYPE budget_alert_type_enum")
        op.execute("DROP TYPE budget_period_enum")
        op.execute("DROP TYPE budget_scope_enum")
