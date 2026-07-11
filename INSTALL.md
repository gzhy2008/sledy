# 技能认定资料收集系统 — 部署指南

## 环境要求

- Python 3.10+
- MySQL 8.0+（生产环境）或 SQLite（开发/测试）
- 邮件服务（用于验证码和通知）

## 快速开始

```bash
# 1. 克隆代码
git clone https://github.com/gzhy2008/SLA_DocSystem.git
cd SLA_DocSystem

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的配置（见下文）

# 5. 启动（开发模式）
python3 app.py
# 访问 http://localhost:5000
```

## 生产环境

```bash
# 在 .env 中设置
DATABASE_URL=mysql+pymysql://user:password@localhost:3306/exam_tool?charset=utf8mb4
FLASK_ENV=production

# 执行数据库迁移
flask db upgrade

# 使用 Gunicorn 启动
gunicorn -c gunicorn.conf.py wsgi:app
```

## .env 配置说明

```ini
# === 必填 ===
SECRET_KEY=随机字符串
MAIL_SERVER=smtp.example.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@example.com
MAIL_PASSWORD=your-password
MAIL_DEFAULT_SENDER=your-email@example.com
ADMIN_INVITE_CODE=INVITE2026
SUPER_ADMIN_PASSWORD=sa

# === 可选 ===
# DATABASE_URL=sqlite:///data.db
# UPLOAD_FOLDER=/path/to/uploads
# FLASK_HOST=0.0.0.0
# FLASK_PORT=5000
# FLASK_ENV=production
```

## 首次使用

1. 超管登录：`sa` / `sa`
2. 系统会强制要求修改密码（至少14位，含大小写、数字、特殊字符）
3. 修改密码后跳转到站点设置，配置站点名称、配色
4. 在 `.env` 中配置好邮箱后重启服务，邮件功能即可使用

## 应急重置超管密码

```bash
source venv/bin/activate
python3 reset_sa_password.py
# 或指定密码: python3 reset_sa_password.py --password 新密码
```
