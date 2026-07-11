import os
import secrets

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
SECRET_KEY_FILE = os.path.join(BASE_DIR, 'secret_key')

def _get_secret_key():
    """获取 SECRET_KEY：环境变量 > 持久化文件 > 随机生成并持久化"""
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r') as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, 'w') as f:
        f.write(key)
    return key

class Config:
    # 优先环境变量，否则从文件读取或生成后持久化（避免重启丢失 session）
    SECRET_KEY = _get_secret_key()
    # 先用 SQLite 快速跑通，以后改 MySQL 只需改这行
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        'DATABASE_URL',
        'sqlite:///' + os.path.join(BASE_DIR, 'data.db')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    # 存储目录（可通过环境变量覆盖，适合生产环境挂载独立磁盘）
    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE_DIR, 'uploads'))
    EXPORT_FOLDER = os.environ.get('EXPORT_FOLDER', os.path.join(BASE_DIR, 'exports'))
    LOG_FOLDER = os.environ.get('LOG_FOLDER', os.path.join(BASE_DIR, 'logs'))
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024  # 单个请求最大10MB
    ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'pdf'}
    # 验证码相关
    CAPTCHA_WIDTH = 120
    CAPTCHA_HEIGHT = 40
    # 邮件配置 (必须通过环境变量设置)
    MAIL_SERVER = os.environ.get('MAIL_SERVER', 'smtp.163.com')
    MAIL_PORT = int(os.environ.get('MAIL_PORT', '25'))
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'False').lower() == 'true'
    MAIL_USE_SSL = os.environ.get('MAIL_USE_SSL', 'False').lower() == 'true'
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME', '')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD', '')
    MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER', os.environ.get('MAIL_USERNAME', ''))

    # Session 安全加固
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    # 邀请码（从环境变量读取）
    ADMIN_INVITE_CODE = os.environ.get('ADMIN_INVITE_CODE', 'INVITE2024')

    # 超级管员初始密码（从环境变量读取，仅首次创建时使用）
    SUPER_ADMIN_PASSWORD = os.environ.get('SUPER_ADMIN_PASSWORD', 'sa')

    # 生产模式（设置 FLASK_ENV=production 后启用）
    @classmethod
    def is_production(cls):
        return os.environ.get('FLASK_ENV', 'development') == 'production'

    # Jinja2 模板缓存（生产环境启用）

    TEMPLATES_AUTO_RELOAD = not (os.environ.get('FLASK_ENV') == 'production')

    # SQLAlchemy 连接池（MySQL 生产环境生效）
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,
        'pool_recycle': 3600,
        'pool_pre_ping': True,
    }
