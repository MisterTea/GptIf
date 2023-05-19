"""Add game feedback

Revision ID: 486f9db41649
Revises: 5c59504f90b2
Create Date: 2023-05-19 17:24:49.575028

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = '486f9db41649'
down_revision = '5c59504f90b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('gamefeedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('feedback', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_gamefeedback_session_id'), 'gamefeedback', ['session_id'], unique=False)
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index(op.f('ix_gamefeedback_session_id'), table_name='gamefeedback')
    op.drop_table('gamefeedback')
    # ### end Alembic commands ###
