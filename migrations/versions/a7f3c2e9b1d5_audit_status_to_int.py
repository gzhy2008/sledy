"""audit_status to int enum

Revision ID: a7f3c2e9b1d5
Revises: 698ec45b7c8c
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7f3c2e9b1d5'
down_revision = '698ec45b7c8c'
branch_labels = None
depends_on = None


def upgrade():
    # 1. 新增整数列
    with op.batch_alter_table('exam_batch', schema=None) as batch_op:
        batch_op.add_column(sa.Column('audit_status_new', sa.Integer(), nullable=True))

    # 2. 映射旧字符串 -> 整数
    op.execute(
        "UPDATE exam_batch SET audit_status_new = "
        "CASE audit_status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 "
        "WHEN 'rejected' THEN 2 ELSE 1 END"
    )

    # 3. 删除旧字符串列
    with op.batch_alter_table('exam_batch', schema=None) as batch_op:
        batch_op.drop_column('audit_status')

    # 4. 重命名新列并设为 NOT NULL
    with op.batch_alter_table('exam_batch', schema=None) as batch_op:
        batch_op.alter_column('audit_status_new', new_column_name='audit_status',
                              existing_type=sa.Integer(), nullable=False,
                              server_default='1')


def downgrade():
    # 反向：整数 -> 字符串
    with op.batch_alter_table('exam_batch', schema=None) as batch_op:
        batch_op.add_column(sa.Column('audit_status_old', sa.String(16), nullable=True))

    op.execute(
        "UPDATE exam_batch SET audit_status_old = "
        "CASE audit_status WHEN 0 THEN 'pending' WHEN 1 THEN 'approved' "
        "WHEN 2 THEN 'rejected' ELSE 'approved' END"
    )

    with op.batch_alter_table('exam_batch', schema=None) as batch_op:
        batch_op.drop_column('audit_status')

    with op.batch_alter_table('exam_batch', schema=None) as batch_op:
        batch_op.alter_column('audit_status_old', new_column_name='audit_status',
                              existing_type=sa.String(16), nullable=False,
                              server_default='approved')
