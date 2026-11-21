"""ticket hotfix flag

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tickets', sa.Column('hotfix', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('tickets', sa.Column('hotfix_by', sa.String(length=30), nullable=True))
    op.add_column('tickets', sa.Column('hotfix_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('tickets', 'hotfix_at')
    op.drop_column('tickets', 'hotfix_by')
    op.drop_column('tickets', 'hotfix')
