#!/usr/bin/env python3
"""CLI 重置超管密码

用法:
    source venv/bin/activate
    python3 reset_sa_password.py
    python3 reset_sa_password.py --password MyNewPass123

用于服务器管理员在终端紧急重置超管密码。
"""

import sys
import os
import secrets

os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app
from models import db, User
from werkzeug.security import generate_password_hash


def generate_password(length=10):
    """生成符合强度要求的密码：至少1大写+1小写+1数字"""
    while True:
        pwd = secrets.token_urlsafe(length)[:length]
        if any(c.isupper() for c in pwd) and any(c.islower() for c in pwd) and any(c.isdigit() for c in pwd):
            return pwd


def main():
    new_password = None
    if '--password' in sys.argv:
        idx = sys.argv.index('--password')
        if idx + 1 < len(sys.argv):
            new_password = sys.argv[idx + 1]

    with app.app_context():
        user = User.query.filter_by(role='super_admin').first()
        if not user:
            print('错误：未找到超级管理员账号')
            sys.exit(1)

        if not new_password:
            new_password = generate_password()
        elif len(new_password) < 8:
            print('错误：密码至少8位')
            sys.exit(1)

        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        print(f'超管密码已重置')
        print(f'用户名: {user.username}')
        print(f'新密码: {new_password}')
        print(f'邮箱:   {user.email or "未设置"}')
        print(f'\n请立即登录并修改密码。')


if __name__ == '__main__':
    main()
