import os
import time
import secrets
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    session, jsonify, send_from_directory
)
from flask_login import LoginManager, login_required, current_user
from werkzeug.security import generate_password_hash

from config import Config
from models import db, User, SiteConfig
from logger_config import setup_logging
from flask_mail import Mail
from flask_migrate import Migrate

# ╔══════════════════════════════════════════════════════════════════╗
# ║                  app.py — 应用入口与基础设施                        ║
# ╚══════════════════════════════════════════════════════════════════╝

app = Flask(__name__)
app.config.from_object(Config)
db.init_app(app)
migrate = Migrate(app, db)
mail = Mail(app)

setup_logging(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = '请先登录'

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)


@app.teardown_request
def teardown_request(exception=None):
    if exception:
        db.session.rollback()
    db.session.remove()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


def init_db():
    """初始化数据库表（开发用 create_all，生产用 flask db upgrade）"""
    from sqlalchemy import inspect
    inspector = inspect(db.engine)
    existing = inspector.get_table_names()
    if not existing:
        db.create_all()
        # 标记 Alembic 基线，避免后续 migrate 冲突
        from flask_migrate import stamp
        stamp()
        if not User.query.filter_by(role='super_admin').first():
            super_admin = User(
                username='sa',
                password_hash=generate_password_hash(app.config['SUPER_ADMIN_PASSWORD']),
                email='sample@mail.com',
                role='super_admin'
            )
            db.session.add(super_admin)
            db.session.commit()


with app.app_context():
    init_db()


def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


@app.context_processor
def inject_csrf_token():
    return {'csrf_token': generate_csrf_token()}


@app.context_processor
def inject_theme():
    """注入主题配置到所有模板"""
    configs = {c.key: c.value for c in SiteConfig.query.all()}
    primary = configs.get('theme_primary', '#667eea')
    secondary = configs.get('theme_secondary', '#764ba2')
    site_title = configs.get('site_title', '技能认定资料收集系统')
    site_subtitle = configs.get('site_subtitle', '一站式技能认定报名、档案审核、批次管理平台')
    site_logo = configs.get('site_logo', '')
    return {
        'theme_primary': primary,
        'theme_secondary': secondary,
        'theme_css_vars': f'--primary:{primary};--secondary:{secondary};',
        'site_title': site_title,
        'site_subtitle': site_subtitle,
        'site_logo': site_logo,
    }


CSRF_EXEMPT_ENDPOINTS = {
    'auth.request_login_verify_code',
    'auth.send_verify_code_api',
    'admin.export_batch',
}


@app.before_request
def csrf_protect():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return None
    if request.endpoint in CSRF_EXEMPT_ENDPOINTS:
        return None
    if request.endpoint in ('static', 'uploaded_file'):
        return None
    token = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    session_token = session.get('_csrf_token')
    if not token or not session_token or not secrets.compare_digest(token, session_token):
        app.logger.warning(f'CSRF validation failed for {request.endpoint} from {request.remote_addr}')
        if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'status': 'error', 'msg': 'CSRF token 验证失败'}), 403
        flash('安全验证失败，请刷新页面后重试', 'danger')
        return redirect(request.referrer or url_for('auth.login'))


@app.before_request
def check_email_verified():
    if not current_user.is_authenticated:
        return None
    if current_user.role == 'super_admin':
        return None

    whitelist = {
        'auth.login', 'auth.logout', 'auth.register', 'static', 'uploaded_file',
        'auth.send_verify_code_api', 'auth.verify_email_code', 'auth.request_login_verify_code',
        'student.edit_profile', 'student.student_profile_view', 'student.student_change_password',
        'student.student_account', 'student.request_change_email', 'student.verify_change_email',
        'admin.admin_account', 'teacher.teacher_account',
        'admin.admin_update_phone', 'teacher.teacher_update_phone',
        'admin.admin_reset_email', 'teacher.teacher_reset_email',
        'teacher.teacher_update_department',
        'auth.dashboard'
    }

    if session.get('force_profile'):
        if request.endpoint not in whitelist:
            flash('请先完善您的邮箱信息', 'warning')
            if current_user.role == 'student':
                return redirect(url_for('student.student_account'))
            elif current_user.role == 'admin':
                return redirect(url_for('admin.admin_account'))
            elif current_user.role == 'headteacher':
                return redirect(url_for('teacher.teacher_account'))
        return None

    if request.endpoint not in whitelist:
        if current_user.role == 'student':
            if not current_user.email_verified:
                flash('您的邮箱尚未验证，请验证后再访问其他页面', 'warning')
                return redirect(url_for('student.student_account'))
        elif current_user.role == 'admin':
            if not current_user.email_verified:
                flash('您的邮箱尚未验证，请验证后再访问其他页面', 'warning')
                return redirect(url_for('admin.admin_account'))
        elif current_user.role == 'headteacher':
            ht = current_user.head_teacher
            if ht:
                if not current_user.email_verified:
                    flash('您的邮箱尚未验证，请验证后再访问其他页面', 'warning')
                    return redirect(url_for('teacher.teacher_account'))
                if not ht.departments:
                    flash('请先选择您所在的系部', 'warning')
                    return redirect(url_for('teacher.teacher_account'))
    return None


from blueprints.auth import auth_bp
from blueprints.student import student_bp
from blueprints.teacher import teacher_bp
from blueprints.admin import admin_bp
from blueprints.super_admin import super_admin_bp

app.register_blueprint(auth_bp)
app.register_blueprint(student_bp)
app.register_blueprint(teacher_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(super_admin_bp)


@app.route('/uploads/<path:filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/api/last_credential')
@login_required
def api_last_credential():
    cred = session.get('last_credential')
    if not cred:
        return jsonify({'status': 'empty', 'msg': '暂无暂存凭证'})
    return jsonify({'status': 'ok', 'data': cred})


@app.errorhandler(Exception)
def handle_exception(e):
    app.logger.error(f'Unhandled exception: {e}', exc_info=True)
    import traceback
    traceback.print_exc()
    if request.is_json:
        return jsonify({'status': 'error', 'msg': '服务器内部错误'}), 500
    return render_template('500.html'), 500


@app.errorhandler(404)
def not_found(e):
    if request.is_json:
        return jsonify({'status': 'error', 'msg': '页面不存在'}), 404
    return render_template('500.html'), 404


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true'
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', '5000'))
    app.run(debug=debug_mode, host=host, port=port)
