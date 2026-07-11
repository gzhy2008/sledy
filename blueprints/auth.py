"""认证与公共路由 Blueprint"""
import re
import time
import random
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, jsonify
)
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_mail import Message

from models import db, User, ClassGroup, ExamBatch, Skill, Notice
from sqlalchemy.orm import joinedload
from services import validate_phone, validate_password_strength, mask_email
from shared import (
    send_verify_code, check_rate_limit,
    _login_attempts, _verify_code_attempts
)

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('auth.dashboard'))

    now = datetime.now()

    # 最新通知（最多5条，已发布、公开可见、未删除）
    notices = Notice.query.filter_by(
        is_published=True, is_public=True, is_deleted=False
    ).order_by(Notice.created_at.desc()).limit(5).all()

    # 最新批次（最多5条，按创建时间倒序）
    latest_batches = ExamBatch.query.options(
        joinedload(ExamBatch.skill)
    ).order_by(ExamBatch.created_at.desc()).limit(5).all()

    return render_template('index.html',
        notices=notices,
        latest_batches=latest_batches,
        now=now)


@auth_bp.route('/notice/<int:notice_id>')
def notice_detail(notice_id):
    n = Notice.query.filter_by(id=notice_id, is_published=True, is_public=True, is_deleted=False).first_or_404()
    return render_template('notice_detail.html', notice=n)


@auth_bp.route('/notices')
def notices_page():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '').strip()

    query = Notice.query.filter_by(is_deleted=False, is_published=True).order_by(Notice.created_at.desc())
    if search:
        query = query.filter(Notice.title.contains(search))
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    notices = query.limit(per_page).offset((page - 1) * per_page).all()
    return render_template('notices.html',
        notices=notices, page=page, total_pages=total_pages, total=total,
        search=search)


@auth_bp.route('/batches/all')
def batches_all():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    status = request.args.get('status', 'active')
    search = request.args.get('search', '').strip()
    now = datetime.now()

    query = ExamBatch.query.options(joinedload(ExamBatch.skill)).order_by(ExamBatch.created_at.desc())
    # 归档批次对陌生人不可见
    query = query.filter(ExamBatch.is_archived == False)
    if status == 'expired':
        query = query.filter(ExamBatch.end_time < now)
    else:
        status = 'active'
        query = query.filter(ExamBatch.end_time >= now)
    # 按工种名称搜索
    if search:
        query = query.filter(ExamBatch.skill.has(Skill.name.contains(search)))
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    batches = query.limit(per_page).offset((page - 1) * per_page).all()
    return render_template('batches_all.html',
        batches=batches, page=page, total_pages=total_pages, total=total,
        now=now, current_status=status, search=search)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        email = request.form.get('email', '').strip()
        role = request.form.get('role', 'student')
        invite = request.form.get('invite', '').strip()
        email_code = request.form.get('email_code', '').strip()

        errors = []
        if not username or len(username) < 3:
            errors.append('用户名至少3个字符')
        if not re.match(r'^[A-Za-z0-9_]+$', username):
            errors.append('用户名只能包含英文字母、数字和下划线')
        if not password:
            errors.append('密码不能为空')
        else:
            valid, msg = validate_password_strength(password)
            if not valid:
                errors.append(msg)
        if not email or '@' not in email:
            errors.append('邮箱格式不正确')
        if User.query.filter_by(username=username).first():
            errors.append('用户名已存在')
        if User.query.filter_by(email=email).first():
            errors.append('该邮箱已被注册')

        if role == 'admin':
            from flask import current_app
            if invite != current_app.config['ADMIN_INVITE_CODE']:
                errors.append('业务管理员邀请码不正确')
        elif role == 'super_admin':
            errors.append('超级管理员不允许注册')
        elif role == 'student':
            saved_code = session.get('email_verify_code')
            code_time = session.get('email_verify_code_time', 0)
            if not saved_code or not email_code:
                errors.append('请获取并输入邮箱验证码')
            elif email_code != saved_code:
                errors.append('验证码错误')
            elif time.time() - code_time > 300:
                errors.append('验证码已过期，请重新获取')
            else:
                session.pop('email_verify_code', None)
                session.pop('email_verify_code_time', None)
                session.pop('send_code_time', None)

        if errors:
            for e in errors:
                flash(e, 'danger')
            return render_template('register.html', form=request.form)

        user = User(
            username=username,
            password_hash=generate_password_hash(password),
            email=email,
            role='student',
            email_verified=True
        )

        db.session.add(user)
        db.session.commit()
        flash('注册成功，请登录', 'success')
        return redirect(url_for('auth.login'))
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        email_code = request.form.get('email_code', '').strip()

        # 速率限制：同一IP+用户名 5分钟内最多5次失败
        rate_key = f"{request.remote_addr}:{username}"
        allowed, retry = check_rate_limit(_login_attempts, rate_key, max_attempts=5, window_seconds=300)
        if not allowed:
            flash(f'登录尝试过于频繁，请 {retry} 秒后再试', 'danger')
            return render_template('login.html')

        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash('用户名或密码错误', 'danger')
            return render_template('login.html')

        if not user.is_active:
            flash('账号已被禁用', 'danger')
            return render_template('login.html')

        # ---------- 邮箱验证逻辑 ----------
        if user.role != 'super_admin':
            if not user.email or '@' not in user.email:
                login_user(user)
                session['force_profile'] = True
                flash('登录成功，请完善您的邮箱信息', 'warning')
                if user.role == 'student':
                    return redirect(url_for('student.student_account'))
                elif user.role == 'admin':
                    return redirect(url_for('admin.admin_account'))
                elif user.role == 'headteacher':
                    return redirect(url_for('teacher.teacher_account'))
                else:
                    return redirect(url_for('auth.dashboard'))

            if not email_code:
                flash('请输入邮箱验证码', 'danger')
                return render_template('login.html', need_verify=True)

            saved_code = session.get('email_verify_code')
            code_time = session.get('email_verify_code_time', 0)
            login_uid = session.get('login_user_id')

            if not saved_code or email_code != saved_code:
                flash('验证码错误', 'danger')
                return render_template('login.html', need_verify=True)
            if time.time() - code_time > 300:
                flash('验证码已过期，请重新获取', 'danger')
                return render_template('login.html', need_verify=True)
            if login_uid != user.id:
                flash('验证码与用户不匹配', 'danger')
                return render_template('login.html', need_verify=True)

            if not user.email_verified:
                user.email_verified = True
                db.session.commit()

            session.pop('force_profile', None)

        session.pop('email_verify_code', None)
        session.pop('email_verify_code_time', None)
        session.pop('login_user_id', None)
        session.pop('last_credential', None)

        login_user(user)
        flash('登录成功', 'success')
        return redirect(url_for('auth.dashboard'))

    return render_template('login.html', need_verify=False)


@auth_bp.route('/request_login_verify_code', methods=['POST'])
def request_login_verify_code():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '').strip()
    if not username or not password:
        return jsonify({'status': 'error', 'msg': '用户名和密码不能为空'})

    rate_key = f"vc:{request.remote_addr}"
    allowed, retry = check_rate_limit(_verify_code_attempts, rate_key, max_attempts=3, window_seconds=300)
    if not allowed:
        return jsonify({'status': 'error', 'msg': f'发送过于频繁，请 {retry} 秒后再试'})

    user = User.query.filter_by(username=username).first()
    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'status': 'error', 'msg': '用户名或密码错误'})

    if not user.is_active:
        return jsonify({'status': 'error', 'msg': '账号已被锁定，无法发送验证码'})

    if not user.email or '@' not in user.email:
        return jsonify({'status': 'error', 'msg': '您的账号未绑定有效邮箱，请直接点击登录，登录后将引导完善资料'})

    last_time = session.get('send_code_time', 0)
    if time.time() - last_time < 60:
        masked = mask_email(user.email)
        return jsonify({
            'status': 'error',
            'msg': '发送过于频繁，请60秒后再试',
            'masked_email': masked,
            'has_email': True
        })

    code = ''.join(random.choices('0123456789', k=6))
    session['email_verify_code'] = code
    session['email_verify_code_time'] = time.time()
    session['send_code_time'] = time.time()
    session['login_user_id'] = user.id

    from flask import current_app
    mail = current_app.extensions['mail']
    msg = Message('技能认定资料收集系统 - 登录验证码', recipients=[user.email])
    msg.charset = 'utf-8'
    msg.body = f'您的登录验证码是：{code}，有效期5分钟，请勿泄露。'
    try:
        mail.send(msg)
        masked = mask_email(user.email)
        return jsonify({
            'status': 'success',
            'msg': '验证码已发送',
            'email': user.email,
            'masked_email': masked,
            'has_email': True
        })
    except Exception as e:
        current_app.logger.error(f'邮件发送失败：{e}')
        masked = mask_email(user.email)
        return jsonify({
            'status': 'error',
            'msg': '邮件发送失败，请稍后再试',
            'masked_email': masked,
            'has_email': True
        })


@auth_bp.route('/send_verify_code', methods=['POST'])
def send_verify_code_api():
    email = request.form.get('email', '').strip()
    if not email or '@' not in email:
        return jsonify({'status': 'error', 'msg': '邮箱格式不正确'})

    if current_user.is_authenticated and not current_user.is_active:
        return jsonify({'status': 'error', 'msg': '账号已被锁定，无法发送验证码'})

    if current_user.is_authenticated:
        existing_user = User.query.filter(User.email == email, User.id != current_user.id).first()
        if existing_user:
            return jsonify({'status': 'error', 'msg': '该邮箱已被其他用户使用'})

    success, msg = send_verify_code(email)
    return jsonify({'status': 'success' if success else 'error', 'msg': msg})


@auth_bp.route('/verify_email_code', methods=['POST'])
@login_required
def verify_email_code():
    email = request.form.get('email', '').strip()
    code = request.form.get('code', '').strip()
    if not email or not code:
        return jsonify({'status': 'error', 'msg': '参数不完整'})

    target_email = session.get('email_verify_target')
    if target_email and email != target_email:
        return jsonify({'status': 'error', 'msg': '邮箱已变更，请重新获取验证码'})

    saved_code = session.get('email_verify_code')
    if not saved_code or code != saved_code:
        return jsonify({'status': 'error', 'msg': '验证码错误'})
    if time.time() - session.get('email_verify_code_time', 0) > 300:
        return jsonify({'status': 'error', 'msg': '验证码已过期'})
    existing_user = User.query.filter(User.email == email, User.id != current_user.id).first()
    if existing_user:
        return jsonify({'status': 'error', 'msg': '该邮箱已被其他用户使用'})
    current_user.email = email
    current_user.email_verified = True
    db.session.commit()
    session.pop('email_verify_code', None)
    session.pop('email_verify_code_time', None)
    session.pop('email_verify_target', None)
    session.pop('force_profile', None)
    return jsonify({'status': 'success', 'msg': '邮箱验证成功'})


@auth_bp.route('/api/classes_by_department')
@login_required
def classes_by_department():
    dept_id = request.args.get('dept_id', type=int)
    if not dept_id:
        return jsonify([])
    classes = ClassGroup.query.filter(
        ClassGroup.department_id == dept_id,
        ClassGroup.is_active == True,
        ClassGroup.is_graduated == False
    ).order_by(ClassGroup.name).all()
    results = [{'id': c.id, 'display': f'{c.name} ({c.class_no})'} for c in classes]
    return jsonify(results)


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.pop('last_credential', None)
    return redirect(url_for('auth.index'))


@auth_bp.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'student':
        return redirect(url_for('student.student_dashboard'))
    elif current_user.role == 'super_admin':
        if check_password_hash(current_user.password_hash, 'sa'):
            flash('请立即修改默认密码，新密码至少14位，含大小写字母、数字和特殊字符', 'warning')
            return redirect(url_for('super_admin.super_admin_account'))
        return redirect(url_for('super_admin.super_admin_dashboard'))
    elif current_user.role == 'admin':
        return redirect(url_for('admin.manage_batches'))
    elif current_user.role == 'super_admin':
        return redirect(url_for('super_admin.super_admin_dashboard'))
    elif current_user.role == 'headteacher':
        return redirect(url_for('teacher.teacher_classes'))
    return '未知角色'
