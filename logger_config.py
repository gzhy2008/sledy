"""
生产级日志配置。
通过环境变量 FLASK_ENV 切换：
  - development（默认）：控制台 + 文件，DEBUG 级别
  - production：仅文件，INFO 级别，三通道分离
"""
import os
import logging
import time
import uuid
from logging.handlers import RotatingFileHandler
from flask import request, g, has_request_context


def setup_logging(app):
    log_dir = app.config.get('LOG_FOLDER', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs'))
    os.makedirs(log_dir, exist_ok=True)

    is_prod = os.environ.get('FLASK_ENV', 'development') == 'production'

    # ── 通用格式 ──────────────────────────────────────────
    detail_fmt = '%(asctime)s [%(levelname)s] %(pathname)s:%(lineno)d | %(message)s'
    brief_fmt  = '%(asctime)s [%(levelname)s] %(message)s'

    # ── 1. 应用日志 (app.log) — INFO 及以上 ───────────────
    app_handler = RotatingFileHandler(
        os.path.join(log_dir, 'app.log'),
        maxBytes=10 * 1024 * 1024, backupCount=10, encoding='utf-8'
    )
    app_handler.setLevel(logging.INFO)
    app_handler.setFormatter(logging.Formatter(detail_fmt))

    # ── 2. 错误日志 (error.log) — ERROR 及以上，独立文件 ──
    error_handler = RotatingFileHandler(
        os.path.join(log_dir, 'error.log'),
        maxBytes=5 * 1024 * 1024, backupCount=5, encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(logging.Formatter(detail_fmt))

    # ── 3. 审计日志 (audit.log) — 独立 logger，不传播 ─────
    audit_logger = logging.getLogger('audit')
    audit_logger.setLevel(logging.INFO)
    audit_handler = RotatingFileHandler(
        os.path.join(log_dir, 'audit.log'),
        maxBytes=10 * 1024 * 1024, backupCount=10, encoding='utf-8'
    )
    audit_handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    audit_logger.addHandler(audit_handler)
    audit_logger.propagate = False

    # ── 配置应用主 logger ─────────────────────────────────
    app.logger.handlers.clear()
    app.logger.setLevel(logging.DEBUG if not is_prod else logging.INFO)
    app.logger.addHandler(app_handler)
    app.logger.addHandler(error_handler)

    if not is_prod:
        console = logging.StreamHandler()
        console.setLevel(logging.DEBUG)
        console.setFormatter(logging.Formatter(brief_fmt))
        app.logger.addHandler(console)

    # ── Werkzeug 访问日志 → app.log ────────────────────────
    wz = logging.getLogger('werkzeug')
    wz.handlers.clear()
    wz.addHandler(app_handler)
    if not is_prod:
        wz.addHandler(logging.StreamHandler())

    # ── 请求追踪（request_id） ─────────────────────────────
    @app.before_request
    def _log_request_id():
        g.request_id = uuid.uuid4().hex[:8]
        g.request_start = time.time()

    @app.after_request
    def _log_response(response):
        if has_request_context():
            ms = (time.time() - g.get('request_start', time.time())) * 1000
            rid = g.get('request_id', '-')
            user = ''
            try:
                from flask_login import current_user
                if current_user.is_authenticated:
                    user = current_user.username
            except Exception:
                pass
            app.logger.info(
                f"[{rid}] {request.remote_addr} {user} "
                f"{request.method} {request.path} → {response.status_code} ({ms:.0f}ms)"
            )
        return response

    app.logger.info(f"日志系统初始化完成 (mode={'production' if is_prod else 'development'})")
