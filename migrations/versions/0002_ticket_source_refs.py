"""ticket source refs

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-21 12:42:44.062268
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('ticket_source_refs',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('ticket_id', sa.BigInteger(), nullable=False),
    sa.Column('source_id', sa.String(length=64), nullable=False),
    sa.Column('external_id', sa.String(length=500), nullable=False),
    sa.Column('url', sa.String(length=1000), nullable=True),
    sa.Column('merged_from_ticket_id', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['source_id'], ['sources.id'], ),
    sa.ForeignKeyConstraint(['ticket_id'], ['tickets.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_id', 'external_id', name='uq_ticket_source_ref')
    )
    op.create_index(op.f('ix_ticket_source_refs_ticket_id'), 'ticket_source_refs', ['ticket_id'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_ticket_source_refs_ticket_id'), table_name='ticket_source_refs')
    op.drop_table('ticket_source_refs')
