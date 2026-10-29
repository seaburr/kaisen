"""preview loop: bundles and project deploy columns

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGBLOB

LongBlob = sa.LargeBinary().with_variant(LONGBLOB(), "mysql")

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('projects', sa.Column('app_id', sa.Integer(), nullable=True))
    op.add_column('projects', sa.Column('app_hostname', sa.String(length=255), nullable=True))
    op.add_column('projects', sa.Column('deploy_status', sa.String(length=32), nullable=True))
    op.add_column('projects', sa.Column('last_deploy', sa.DateTime(), nullable=True))
    op.create_table('bundles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('project_id', sa.Integer(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('data', LongBlob, nullable=False),
    sa.Column('created', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_bundles_project_id'), 'bundles', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_bundles_project_id'), table_name='bundles')
    op.drop_table('bundles')
    op.drop_column('projects', 'last_deploy')
    op.drop_column('projects', 'deploy_status')
    op.drop_column('projects', 'app_hostname')
    op.drop_column('projects', 'app_id')
