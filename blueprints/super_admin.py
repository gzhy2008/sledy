"""超级管理员路由 Blueprint"""
import os
import re
import time
import random
import string
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, jsonify, current_app
)
from flask_login import login_required, current_user
from flask_mail import Message
from werkzeug.security import generate_password_hash, check_password_hash

from models import db, User, UserProfile, ClassGroup, HeadTeacher, ExamBatch, Student, AdminProfile, Department, Skill, SiteConfig, Notice
from sqlalchemy.orm import joinedload
from utils import validate_id_number_checksum, role_required
from services import validate_phone, ROLE_NAMES, generate_random_password, mask_email, validate_password_strength, validate_strong_password
from shared import (
    send_verify_code, send_credentials_notification, audit_log,
    get_admin_department_ids, check_rate_limit, _login_attempts
)

super_admin_bp = Blueprint('super_admin', __name__)


@super_admin_bp.route('/super_admin')
@login_required
@role_required('super_admin')
def super_admin_dashboard():
    role = request.args.get('role', 'admin')
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    is_search = len(search) >= 2

    query = User.query
    if role != 'all':
        query = query.filter(User.role == role)

    if is_search:
        query = query.outerjoin(UserProfile, User.profile)\
                     .outerjoin(AdminProfile, User.admin_profile)\
                     .outerjoin(HeadTeacher, User.head_teacher)\
                     .filter(
                         db.or_(
                             User.username.contains(search),
                             UserProfile.name.contains(search),
                             AdminProfile.name.contains(search),
                             HeadTeacher.name.contains(search)
                         )
                     ).distinct()

    query = query.options(
        joinedload(User.admin_profile),
        joinedload(User.head_teacher),
        joinedload(User.profile)
    ).order_by(User.created_at.desc())

    if is_search:
        total = query.count()
        users = query.limit(per_page).offset((page - 1) * per_page).all()
        total_pages = (total + per_page - 1) // per_page
    else:
        users = query.limit(per_page).all()
        total = None
        total_pages = None

    # 查询所有系部（供修改管辖系部模态框使用）
    all_departments = Department.query.filter_by(is_deleted=False).order_by(Department.id).all()

    return render_template('super_admin.html',
                           users=users,
                           current_role=role,
                           search=search,
                           page=page,
                           total_pages=total_pages,
                           is_search=is_search,
                           all_departments=all_departments)   # 新增

#----------------------
@super_admin_bp.route('/super_admin/create_admin', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def create_admin():
    departments = Department.query.filter_by(is_deleted=False).all()
    # 获取当前活跃的全局管理员（用于显示提示）
    current_global = AdminProfile.query.join(User).filter(
        AdminProfile.is_global == True,
        User.is_active == True
    ).first()
    
    # 统计系部被普通管理员占用的情况（用于提示）
    dept_usage = {}
    limited_admins = AdminProfile.query.filter_by(is_global=False).options(
        joinedload(AdminProfile.departments)
    ).all()
    for ap in limited_admins:
        for dept in ap.departments:
            if dept.id not in dept_usage:
                dept_usage[dept.id] = []
            dept_usage[dept.id].append(ap.name)
    
    if request.method == 'POST':
        # 检查系部是否存在
        if Department.query.count() == 0:
            flash('请先创建系部，再创建业务管理员', 'danger')
            return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)
        
        name = request.form.get('name', '').strip()
        id_number = request.form.get('id_number', '').strip()
        is_global = request.form.get('is_global') == '1'
        department_ids = request.form.getlist('department_ids')
        
        # 基本校验
        if not name or not id_number:
            flash('所有字段均为必填', 'danger')
            return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)
        
        if not re.match(r'^\d{17}[\dXx]$', id_number):
            flash('身份证号格式不正确', 'danger')
            return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)
        
        # 身份证号唯一性检查（所有角色）
        if AdminProfile.query.filter_by(id_number=id_number).first():
            flash('该身份证号已存在，无法重复创建', 'danger')
            return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)
        
        if UserProfile.query.filter_by(id_number=id_number).first():
            flash('该身份证号已被学生使用，无法创建', 'danger')
            return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)
        
        if HeadTeacher.query.filter_by(id_number=id_number).first():
            flash('该身份证号已被班主任使用，无法创建', 'danger')
            return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)
        
        if not is_global and not department_ids:
            flash('请选择至少一个管辖系部', 'danger')
            return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)

        # 普通管理员最多选择 N-1 个系部
        if not is_global:
            total_depts = Department.query.filter_by(is_deleted=False).count()
            if len(department_ids) >= total_depts:
                flash(f'普通管理员最多只能管辖 {total_depts - 1} 个系部（系部总数 {total_depts} 个）', 'danger')
                return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)
        
        # 全局业务管理员处理
        if is_global:
            existing_global = AdminProfile.query.join(User).filter(
                AdminProfile.is_global == True,
                User.is_active == True
            ).first()
            if existing_global:
                # 锁定旧全局管理员
                existing_global.user.is_active = False
                db.session.flush()
                flash(f'原全局管理员 {existing_global.name} 已被锁定', 'warning')
        
        # 生成账号
        username = 'ad' + ''.join(random.choices(string.digits, k=7))
        while User.query.filter_by(username=username).first():
            username = 'ad' + ''.join(random.choices(string.digits, k=7))
        
        new_pwd = generate_random_password()
        user = User(
            username=username,
            password_hash=generate_password_hash(new_pwd),
            role='admin'
        )
        db.session.add(user)
        db.session.flush()
        
        # 创建管理员资料
        ap = AdminProfile(
            user_id=user.id,
            name=name,
            id_number=id_number,
            is_global=is_global
        )
        if not is_global and department_ids:
            ap.departments = Department.query.filter(Department.id.in_(department_ids)).all()
        
        db.session.add(ap)
        db.session.commit()
        
        audit_log('CREATE_ADMIN', f'{name}({username})')
        # 将凭证邮件发送给操作者留底
        email_ok, email_msg = send_credentials_notification(current_user, name, username, 'admin', new_pwd, '创建')
        flash(f'业务管理员 {name} 创建成功！账号：{username}，初始密码：{new_pwd}，账户凭证留底已发往 {mask_email(current_user.email)} 邮箱', 'success')
        if not email_ok:
            flash(email_msg, 'warning')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    
    return render_template('create_admin.html', departments=departments, dept_usage=dept_usage, current_global=current_global)

#-------------------------
@super_admin_bp.route('/super_admin/replace_global', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def replace_global_admin():
    # 获取当前全局管理员
    current_global = AdminProfile.query.filter_by(is_global=True).first()
    if not current_global:
        flash('当前没有全局管理员，请直接创建', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        id_number = request.form.get('id_number', '').strip()
        email = request.form.get('email', '').strip()

        if not name or not id_number or not email:
            flash('所有字段均为必填', 'danger')
            return render_template('replace_global_admin.html', current_global=current_global)

        if not re.match(r'^\d{17}[\dXx]$', id_number):
            flash('身份证号格式不正确', 'danger')
            return render_template('replace_global_admin.html', current_global=current_global)

        # 检查身份证号是否已被使用（所有角色）
        if AdminProfile.query.filter(AdminProfile.id_number == id_number, AdminProfile.id != current_global.id).first():
            flash('该身份证号已被其他业务管理员使用', 'danger')
            return render_template('replace_global_admin.html', current_global=current_global)
        if UserProfile.query.filter_by(id_number=id_number).first():
            flash('该身份证号已被学生使用', 'danger')
            return render_template('replace_global_admin.html', current_global=current_global)
        if HeadTeacher.query.filter_by(id_number=id_number).first():
            flash('该身份证号已被班主任使用', 'danger')
            return render_template('replace_global_admin.html', current_global=current_global)

        # 锁定旧全局管理员
        #current_global.user.is_active = False

        # 清除旧档案信息
        current_global.name = name
        current_global.id_number = id_number

        # 重置密码
        new_pwd = generate_random_password()
        current_global.user.password_hash = generate_password_hash(new_pwd)

        # 更新邮箱
        current_global.user.email = email
        current_global.user.email_verified = False

        db.session.commit()

        # 将凭证邮件发送给操作者留底
        email_ok, email_msg = send_credentials_notification(current_user, name, current_global.user.username, 'admin', new_pwd, '重置')

        # 发送邮件通知
        if email:
            try:
                msg = Message(
                    '技能认定资料收集系统 - 全局管理员变更通知',
                    recipients=[email]
                )
                msg.charset = 'utf-8'
                msg.body = f'''您好，{name}：

您已被任命为系统全局管理员。
用户名：{current_global.user.username}
初始密码：{new_pwd}

请及时登录系统并修改密码。

—— 技能认定资料收集系统'''
                current_app.extensions['mail'].send(msg)
            except Exception as e:
                current_app.logger.error(f'通知邮件发送失败：{e}')

        audit_log('REPLACE_GLOBAL_ADMIN', f'{name}({current_global.user.username})')
        flash(f'全局管理员已更换为 {name}，用户名：{current_global.user.username}，初始密码已重置，账户凭证留底已发往 {mask_email(current_user.email)} 邮箱', 'success')
        if not email_ok:
            flash(email_msg, 'warning')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    return render_template('replace_global_admin.html', current_global=current_global)

#-------------------------
@super_admin_bp.route('/super_admin/upgrade_to_global/<int:admin_id>', methods=['POST'])
@login_required
@role_required('super_admin')
def upgrade_to_global_admin(admin_id):
    admin = AdminProfile.query.get_or_404(admin_id)
    
    if admin.is_global:
        flash('该管理员已经是全局管理员', 'warning')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    
    # 检查是否已有活跃的全局管理员
    existing_global = AdminProfile.query.join(User).filter(AdminProfile.is_global == True, User.is_active == True).first()
    if existing_global:
        # 锁定旧全局管理员
        existing_global.user.is_active = False
        flash(f'原全局管理员 {existing_global.name} 已被锁定', 'warning')
    
    # 清除系部（全局管理员不管理特定系部）
    admin.departments = []
    
    # 升级为全局
    admin.is_global = True
    db.session.commit()
    
    flash(f'{admin.name} 已升级为全局管理员', 'success')
    return redirect(url_for('super_admin.super_admin_dashboard'))
#-------------------------
@super_admin_bp.route('/super_admin/downgrade_from_global/<int:admin_id>', methods=['POST'])
@login_required
@role_required('super_admin')
def downgrade_from_global(admin_id):
    admin = AdminProfile.query.get_or_404(admin_id)
    
    if not admin.is_global:
        flash('该管理员不是全局管理员，无法降级', 'warning')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    
    if not admin.user.is_active:
        flash('无法操作已锁定的管理员', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    
    # 检查是否还有其他活跃的全局管理员（唯一性检查）
    other_global = AdminProfile.query.join(User).filter(
        AdminProfile.is_global == True,
        AdminProfile.id != admin.id,
        User.is_active == True
    ).first()
    
    if not other_global:
        flash('无法降级最后一个活跃的全局管理员，请先创建新的全局管理员或升级其他管理员', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    
    # 降级为普通管理员
    admin.is_global = False
    admin.departments = []
    db.session.commit()
    
    flash(f'{admin.name} 已降级为普通管理员，请为其分配管辖系部', 'success')
    return redirect(url_for('super_admin.super_admin_dashboard'))
#-------------------------
@super_admin_bp.route('/super_admin/transfer_headteacher/<int:ht_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def transfer_headteacher_classes(ht_id):
    # 如果是 admin 角色，必须为全局管理员
    if current_user.role == 'admin':
        if not current_user.admin_profile or not current_user.admin_profile.is_global:
            flash('您没有权限进行此操作，仅限全局管理员', 'danger')
            return redirect(url_for('auth.dashboard'))
    
    current_ht = HeadTeacher.query.get_or_404(ht_id)
    
    if request.method == 'POST':
        target_ht_id = request.form.get('target_ht_id', '').strip()
        lock_target = request.form.get('lock_target') == '1'
        transfer_empty_depts = request.form.get('transfer_empty_depts') == '1'

        if not target_ht_id:
            flash('请选择目标班主任', 'danger')
            return redirect(url_for('super_admin.transfer_headteacher_classes', ht_id=ht_id))
        
        target_ht = HeadTeacher.query.get(int(target_ht_id))
        if not target_ht:
            flash('目标班主任不存在', 'danger')
            return redirect(url_for('super_admin.transfer_headteacher_classes', ht_id=ht_id))
        
        if target_ht.id == current_ht.id:
            flash('不能选择自己作为目标班主任', 'danger')
            return redirect(url_for('super_admin.transfer_headteacher_classes', ht_id=ht_id))

        # 4. 邮箱验证状态检查：双方必须都已验证
        if not current_ht.user.email_verified or not target_ht.user.email_verified:
            flash('双方班主任的邮箱必须都已验证，才能转移班级。请先完成邮箱验证', 'danger')
            return redirect(url_for('super_admin.transfer_headteacher_classes', ht_id=ht_id))

        # 检查目标班主任是否有班级
        if not target_ht.classes:
            flash(f'目标班主任 {target_ht.name} 当前没有关联任何班级，无需转移', 'warning')
            return redirect(url_for('super_admin.transfer_headteacher_classes', ht_id=ht_id))

       

        # 转移班级：将目标班主任的班级全部转给当前班主任
        classes_to_transfer = list(target_ht.classes)
        target_ht.classes = []
        for c in classes_to_transfer:
            current_ht.classes.append(c)

        # 同步更新当前班主任的系部关联
        all_depts = set()
        for c in current_ht.classes:
            if c.department:
                all_depts.add(c.department)

        # 如果勾选了"同时转移无班级的系部"
        if transfer_empty_depts:
            for d in target_ht.departments:
                all_depts.add(d)

        current_ht.departments = list(all_depts)

        # 锁定目标班主任（可选）
        if lock_target:
            target_ht.user.is_active = False
        
        db.session.commit()
        
        lock_msg = '并已锁定' if lock_target else '未锁定'
        flash(f'已将 {target_ht.name} 管理的 {len(classes_to_transfer)} 个班级转移给 {current_ht.name}，{lock_msg}', 'success')
        return redirect(url_for('admin.manage_teachers'))
    
    # 查询所有其他班主任（排除自身和已锁定的）
    other_headteachers = HeadTeacher.query.join(User).filter(
        HeadTeacher.id != ht_id,
        User.is_active == True
    ).all()
    
    departments = Department.query.filter_by(is_deleted=False).order_by(Department.name).all()

    # 构建教师数据（用于前端展示）
    teacher_data = []
    for ht in other_headteachers:
        teacher_data.append({
            'id': ht.id,
            'name': ht.name,
            'deptIds': [d.id for d in ht.departments],
            'classes': '、'.join([f'{c.name}（{c.class_no}）' for c in ht.classes]) or '暂无班级'
        })

    return render_template('transfer_headteacher.html',
                        current_ht=current_ht,
                        other_headteachers=other_headteachers,
                        departments=departments,
                        teacher_data=teacher_data)

#-------------------------
@super_admin_bp.route('/super_admin/search_users')
@login_required
@role_required('super_admin')
def search_users():
    keyword = request.args.get('keyword', '').strip()
    if not keyword or len(keyword) < 1:
        return jsonify([])

    results = []
    profiles = UserProfile.query.filter(
        (UserProfile.name.contains(keyword)) | (UserProfile.phone.contains(keyword))
    ).all()
    for p in profiles:
        results.append({
            'user_id': p.user_id,
            'username': p.user.username,
            'role': 'student',
            'name': p.name,
            'phone': p.phone or '',
            'id_number': p.id_number,
            'type': 'profile'
        })

    teachers = HeadTeacher.query.filter(
        (HeadTeacher.name.contains(keyword)) | (HeadTeacher.phone.contains(keyword))
    ).all()
    for t in teachers:
        results.append({
            'user_id': t.user_id,
            'username': t.user.username,
            'role': 'headteacher',
            'name': t.name,
            'phone': t.phone or '',
            'id_number': t.id_number,
            'type': 'headteacher'
        })

    admins = AdminProfile.query.filter(
        (AdminProfile.name.contains(keyword)) | (AdminProfile.phone.contains(keyword))
    ).all()
    for a in admins:
        results.append({
            'user_id': a.user_id,
            'username': a.user.username,
            'role': 'admin',
            'name': a.name,
            'phone': a.phone or '',
            'id_number': a.id_number,
            'type': 'admin_profile'
        })

    users = User.query.filter(User.username.contains(keyword)).all()
    for u in users:
        if not any(r['user_id'] == u.id for r in results):
            id_number = ''
            if u.profile:
                id_number = u.profile.id_number
            elif u.head_teacher:
                id_number = u.head_teacher.id_number
            elif u.admin_profile:
                id_number = u.admin_profile.id_number
            results.append({
                'user_id': u.id,
                'username': u.username,
                'role': u.role,
                'name': u.username,
                'phone': '',
                'id_number': id_number,
                'type': 'user'
            })

    return jsonify(results)

#-----------------------------
@super_admin_bp.route('/super_admin/verify_email/<int:user_id>', methods=['POST'])
@login_required
@role_required('super_admin')
def super_admin_verify_email(user_id):
    user = User.query.get_or_404(user_id)
    if not user.email or '@' not in user.email:
        flash('该用户尚未填写邮箱，无法验证', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    # 统一使用 User 表的 email_verified 字段
    user.email_verified = True
    db.session.commit()
    flash(f'已手动验证用户 {user.username} 的邮箱', 'success')
    return redirect(url_for('super_admin.super_admin_dashboard'))

#------------------------------------
@super_admin_bp.route('/super_admin/update_id_number', methods=['POST'])
@login_required
@role_required('super_admin')
def super_admin_update_id_number():
    user_id = request.form.get('user_id', type=int)
    new_id_number = request.form.get('new_id_number', '').strip()

    if not user_id or not new_id_number:
        flash('参数错误', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    if not validate_id_number_checksum(new_id_number):
        flash('新身份证号格式不正确', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash('不能修改自己的身份证号', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    if (UserProfile.query.filter(UserProfile.id_number == new_id_number, UserProfile.user_id != user.id).first() or
        HeadTeacher.query.filter(HeadTeacher.id_number == new_id_number, HeadTeacher.user_id != user.id).first() or
        AdminProfile.query.filter(AdminProfile.id_number == new_id_number, AdminProfile.user_id != user.id).first()):
        flash('该身份证号已被其他人使用', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    old_id_number = ''
    try:
        if user.role == 'student' and user.profile:
            profile = user.profile
            old_id_number = profile.id_number
            active_regs = Student.query.filter(
                Student.user_id == user.id,
                Student.status.in_(['pending', 'approved'])
            ).count()
            if active_regs > 0:
                flash('该学生有未完成或已通过的报名，无法修改身份证号', 'danger')
                return redirect(url_for('super_admin.super_admin_dashboard'))
            profile.id_number = new_id_number
            Student.query.filter(Student.user_id == user.id).update({'id_number': new_id_number})

        elif user.role == 'headteacher' and user.head_teacher:
            ht = user.head_teacher
            old_id_number = ht.id_number
            ht.id_number = new_id_number

        elif user.role == 'admin' and user.admin_profile:
            ap = user.admin_profile
            old_id_number = ap.id_number
            ap.id_number = new_id_number
        else:
            flash('无法确定用户类型或档案不存在', 'danger')
            return redirect(url_for('super_admin.super_admin_dashboard'))

        db.session.commit()
        current_app.logger.warning(
            f'SUPERADMIN 身份证号修改: {current_user.username} 修改了 {user.username}(ID:{user.id}) '
            f'的身份证号 从 {old_id_number} 到 {new_id_number}'
        )
        flash(f'身份证号已成功修改为 {new_id_number}。相关文件可能需手动处理。', 'success')
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'修改身份证号失败: {e}')
        flash('修改失败，请重试', 'danger')

    return redirect(url_for('super_admin.super_admin_dashboard'))

#----------------------------
@super_admin_bp.route('/super_admin/update_email/<int:user_id>', methods=['POST'])
@login_required
@role_required('super_admin')
def super_admin_update_email(user_id):
    user = User.query.get_or_404(user_id)
    new_email = request.form.get('new_email', '').strip()
    if not new_email or '@' not in new_email:
        flash('邮箱格式不正确', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    # 新邮箱与旧邮箱相同检查
    if user.email and user.email == new_email:
        flash('新邮箱与当前邮箱相同，无需修改', 'warning')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    # 唯一性检查
    if User.query.filter(User.email == new_email, User.id != user.id).first():
        flash('该邮箱已被其他用户使用', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    old_email = user.email
    user.email = new_email
    user.email_verified = False   # 强制重新验证
    db.session.commit()

    # 发送邮件通知到新邮箱
    try:
        msg = Message(
            '技能认定资料收集系统 - 邮箱变更通知',
            recipients=[new_email]
        )
        msg.charset = 'utf-8'
        msg.body = f'''您好，{user.username}：

    您的邮箱已被管理员从 {old_email} 变更为 {new_email}。

    请您使用新邮箱登录系统，并重新验证邮箱。

    如果这不是您本人操作，请联系管理员。

    —— 技能认定资料收集系统'''
        current_app.extensions['mail'].send(msg)
    except Exception as e:
        current_app.logger.error(f'通知邮件发送失败：{e}')

    flash(f'已将用户 {user.username} 的邮箱从 {old_email} 更换为 {new_email}，已发送通知邮件至新邮箱。', 'success')
    return redirect(url_for('super_admin.super_admin_dashboard'))

#----------------------------
@super_admin_bp.route('/super_admin/toggle_user/<int:user_id>')
@login_required
@role_required('super_admin')
def toggle_user(user_id):
    user = User.query.get(user_id)
    if not user:
        flash('用户不存在', 'danger')
    elif user.role == 'super_admin' or user.id == current_user.id:
        flash('不能操作超级管理员或自身', 'warning')
    elif user.role == 'admin' and user.admin_profile and user.admin_profile.is_global:
        if user.is_active:
            # 锁定：不允许
            flash('全局管理员不可锁定', 'warning')
        else:
            # 解锁：检查是否已有活跃的全局管理员
            active_global = AdminProfile.query.join(User).filter(
                AdminProfile.is_global == True,
                AdminProfile.user_id != user.id,
                User.is_active == True
            ).first()
            if active_global:
                # 已有活跃全局，将当前用户降级为普通管理员后解锁
                user.admin_profile.is_global = False
                user.admin_profile.departments = []
                user.is_active = True
                db.session.commit()
                audit_log('TOGGLE_USER', f'{user.username} -> 降级解锁(全局管理员已存在:{active_global.name})')
                flash(f'已存在活跃全局管理员 {active_global.name}，{user.username} 已降级为普通管理员并解锁，请为其分配管辖系部', 'success')
            else:
                # 无活跃全局，直接解锁
                user.is_active = True
                db.session.commit()
                audit_log('TOGGLE_USER', f'{user.username} -> 解锁')
                flash(f'用户 {user.username} 已解锁', 'success')
    else:
        user.is_active = not user.is_active
        db.session.commit()
        audit_log('TOGGLE_USER', f'{user.username} -> {"启用" if user.is_active else "禁用"}')
        flash(f'用户 {user.username} 状态已切换', 'success')
    return redirect(url_for('super_admin.super_admin_dashboard'))

#---------------------
@super_admin_bp.route('/super_admin/reset_password/<int:user_id>')
@login_required
@role_required('super_admin')
def reset_password(user_id):
    user = User.query.get(user_id)
    if not user:
        flash('用户不存在', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    if user.id == current_user.id:
        flash('不能重置自己的密码', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    new_pwd = generate_random_password()
    user.password_hash = generate_password_hash(new_pwd)
    db.session.commit()
    # 获取目标用户的显示名称并发送凭证邮件给操作者留底
    target_name = user.username  # 兜底用用户名
    if user.profile:
        target_name = user.profile.name
    elif user.head_teacher:
        target_name = user.head_teacher.name
    elif user.admin_profile:
        target_name = user.admin_profile.name
    email_ok, email_msg = send_credentials_notification(current_user, target_name, user.username, user.role, new_pwd, '重置')
    audit_log('RESET_USER_PASSWORD', f'{target_name}({user.username})')
    flash(f'用户 {user.username} 的密码已重置为 {new_pwd}，账户凭证留底已发往 {mask_email(current_user.email)} 邮箱', 'success')
    if not email_ok:
        flash(email_msg, 'warning')
    return redirect(url_for('super_admin.super_admin_dashboard'))

#---------------------
@super_admin_bp.route('/super_admin/reset_admin_email/<int:user_id>', methods=['POST'])
@login_required
@role_required('super_admin')
def reset_admin_email(user_id):
    user = User.query.get_or_404(user_id)
    if user.role != 'admin' or not user.admin_profile:
        flash('只能重置业务管理员', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    if not user.email or '@' not in user.email:
        flash(f'用户 {user.username} 尚未填写邮箱或格式不正确，无法重置邮箱验证状态', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    # 统一操作 User 表
    user.email_verified = False
    db.session.commit()
    flash(f'已重置 {user.username} 的邮箱验证状态，业务管理员可重新验证邮箱', 'success')
    return redirect(url_for('super_admin.super_admin_dashboard'))

#----------------
@super_admin_bp.route('/super_admin/update_admin_departments', methods=['POST'])
@login_required
@role_required('super_admin')
def update_admin_departments():
    user_id = request.form.get('user_id', type=int)   # 从表单获取
    if not user_id:
        flash('参数错误', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    user = User.query.get_or_404(user_id)
    if user.role != 'admin' or not user.admin_profile:
        flash('只能修改业务管理员的管辖系部', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    if user.admin_profile.is_global:
        flash('全局管理员无需设置管辖系部', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    department_ids = request.form.getlist('department_ids')
    if not department_ids:
        flash('请选择至少一个管辖系部', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))
    # 普通管理员最多选择 N-1 个系部
    total_depts = Department.query.filter_by(is_deleted=False).count()
    if len(department_ids) >= total_depts:
        flash(f'普通管理员最多只能管辖 {total_depts - 1} 个系部（系部总数 {total_depts} 个）', 'danger')
        return redirect(url_for('super_admin.super_admin_dashboard'))

    user.admin_profile.departments = Department.query.filter(Department.id.in_(department_ids)).all()
    db.session.commit()
    flash(f'已更新 {user.username} 的管辖系部', 'success')
    return redirect(url_for('super_admin.super_admin_dashboard'))


@super_admin_bp.route('/super_admin/theme', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def theme_settings():
    if request.method == 'POST':
        primary = request.form.get('primary', '#667eea').strip()
        secondary = request.form.get('secondary', '#764ba2').strip()
        site_title = request.form.get('site_title', '').strip()
        site_subtitle = request.form.get('site_subtitle', '').strip()

        # 保存文本配置
        for k, v in [('theme_primary', primary), ('theme_secondary', secondary),
                      ('site_title', site_title), ('site_subtitle', site_subtitle)]:
            cfg = SiteConfig.query.filter_by(key=k).first()
            if cfg:
                cfg.value = v
            else:
                db.session.add(SiteConfig(key=k, value=v))

        # 处理 Logo 上传
        logo = request.files.get('site_logo')
        if logo and logo.filename:
            ext = logo.filename.rsplit('.', 1)[-1].lower()
            if ext in ('png', 'jpg', 'jpeg', 'ico'):
                filename = f'site_logo.{ext}'
                logo.save(os.path.join(current_app.config['UPLOAD_FOLDER'], filename))
                cfg = SiteConfig.query.filter_by(key='site_logo').first()
                if cfg:
                    cfg.value = filename
                else:
                    db.session.add(SiteConfig(key='site_logo', value=filename))

        # 更新超管邮箱
        sa_email = request.form.get("sa_email", "").strip()
        sa_user = User.query.filter_by(role="super_admin").first()
        if sa_user and sa_email:
            sa_user.email = sa_email

        db.session.commit()
        flash('主题配置已更新', 'success')
        return redirect(url_for('super_admin.theme_settings'))

    configs = {c.key: c.value for c in SiteConfig.query.all()}
    return render_template('theme_settings.html',
        primary=configs.get('theme_primary', '#667eea'),
        secondary=configs.get('theme_secondary', '#764ba2'),
        site_title=configs.get('site_title', ''),
        site_subtitle=configs.get('site_subtitle', ''),
        site_logo=configs.get('site_logo', ''),
        sa_email=User.query.filter_by(role='super_admin').first().email if User.query.filter_by(role='super_admin').first() else '')



# ===================== 通知管理 =====================
def _can_manage_notices():
    if current_user.role == 'super_admin':
        return True
    if current_user.role == 'admin' and current_user.admin_profile and current_user.admin_profile.is_global:
        return True
    return False


@super_admin_bp.route('/super_admin/notices')
@login_required
def manage_notices():
    if not _can_manage_notices():
        flash('无权限访问通知管理', 'danger')
        return redirect(url_for('auth.dashboard'))
    status = request.args.get('status', 'published')
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 20

    query = Notice.query.filter_by(is_deleted=False)
    if status == 'published':
        query = query.filter_by(is_published=True)
    elif status == 'draft':
        query = query.filter_by(is_published=False)
    if search:
        query = query.filter(Notice.title.contains(search))
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    notices = query.order_by(Notice.created_at.desc()).limit(per_page).offset((page - 1) * per_page).all()
    return render_template('manage_notices.html',
        base_url='super_admin.manage_notices',
        save_endpoint='super_admin.save_notice',
        toggle_endpoint='super_admin.toggle_notice',
        delete_endpoint='super_admin.delete_notice',
        notices=notices, page=page, total_pages=total_pages, total=total,
        current_status=status, search=search)


@super_admin_bp.route('/super_admin/notice/save', methods=['POST'])
@login_required
def save_notice():
    if not _can_manage_notices():
        flash('无权限', 'danger')
        return redirect(url_for('auth.dashboard'))
    redirect_status = request.form.get('redirect_status', 'published')
    notice_id = request.form.get('id', type=int)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    importance = request.form.get('importance', 'normal')
    is_public = request.form.get('is_public') == '1'
    is_published = request.form.get('is_published') == '1'

    if not title:
        flash('标题不能为空', 'danger')
        return redirect(url_for('super_admin.manage_notices', status=redirect_status))

    if notice_id:
        n = Notice.query.get(notice_id)
        if n:
            n.title = title
            n.content = content
            n.importance = importance
            n.is_public = is_public
            n.is_published = is_published
    else:
        n = Notice(title=title, content=content, importance=importance,
                   is_public=is_public, is_published=is_published)
        db.session.add(n)
    db.session.commit()
    flash('通知已保存', 'success')
    return redirect(url_for('super_admin.manage_notices', status=redirect_status))


@super_admin_bp.route('/super_admin/notice/<int:notice_id>/toggle', methods=['POST'])
@login_required
def toggle_notice(notice_id):
    redirect_status = request.form.get('redirect_status', 'published')
    if not _can_manage_notices():
        flash('无权限', 'danger')
        return redirect(url_for('auth.dashboard'))
    n = Notice.query.get_or_404(notice_id)
    n.is_published = not n.is_published
    db.session.commit()
    flash('已发布' if n.is_published else '已撤回', 'success')
    return redirect(url_for('super_admin.manage_notices', status=redirect_status))


@super_admin_bp.route('/super_admin/notice/<int:notice_id>/delete', methods=['POST'])
@login_required
def delete_notice(notice_id):
    redirect_status = request.form.get('redirect_status', 'published')
    if not _can_manage_notices():
        flash('无权限', 'danger')
        return redirect(url_for('auth.dashboard'))
    n = Notice.query.get_or_404(notice_id)
    n.is_deleted = True
    db.session.commit()
    flash('通知已删除', 'success')
    return redirect(url_for('super_admin.manage_notices', status=redirect_status))


# ===================== 超管个人资料 & 账户 =====================
@super_admin_bp.route('/super_admin/profile', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def super_admin_profile():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        code = request.form.get('code', '').strip()
        if not email or '@' not in email:
            flash('邮箱格式不正确', 'danger')
        elif not code:
            flash('请输入验证码', 'danger')
        else:
            saved = session.get('sa_email_verify_code')
            if not saved or code != saved:
                flash('验证码错误', 'danger')
            elif time.time() - session.get('sa_email_verify_code_time', 0) > 300:
                flash('验证码已过期', 'danger')
            else:
                current_user.email = email
                current_user.email_verified = True
                db.session.commit()
                session.pop('sa_email_verify_code', None)
                session.pop('sa_email_verify_code_time', None)
                flash('邮箱已更新并验证', 'success')
        return redirect(url_for('super_admin.super_admin_profile'))
    return render_template('super_admin_profile.html', user=current_user)


@super_admin_bp.route('/super_admin/account', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def super_admin_account():
    if request.method == 'POST':
        old_pw = request.form.get('old_password', '')
        new_pw = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not current_user.email or not current_user.email_verified:
            flash('邮箱未配置，修改密码后无法收到留底通知。请尽快配置邮箱。', 'warning')
        if not check_password_hash(current_user.password_hash, old_pw):
            flash('当前密码错误', 'danger')
        elif new_pw != confirm:
            flash('两次密码不一致', 'danger')
        else:
            valid, msg = validate_strong_password(new_pw)
            if not valid:
                flash(msg, 'danger')
            else:
                current_user.password_hash = generate_password_hash(new_pw)
                db.session.commit()
                try:
                    mail = current_app.extensions['mail']
                    body = (f'\u8d85\u7ea7\u7ba1\u7406\u5458\u5bc6\u7801\u5df2\u4fee\u6539\u3002\n\n'
                            f'\u7528\u6237\u540d: {current_user.username}\n'
                            f'\u4fee\u6539\u65f6\u95f4: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n\n'
                            '\u5982\u975e\u672c\u4eba\u64cd\u4f5c\uff0c\u8bf7\u7acb\u5373\u8054\u7cfb\u670d\u52a1\u5668\u7ba1\u7406\u5458\u91cd\u7f6e\u5bc6\u7801\u3002\n\n'
                            '\u2014\u2014 \u6280\u80fd\u8ba4\u5b9a\u8d44\u6599\u6536\u96c6\u7cfb\u7edf')
                    msg_obj = Message('\u6280\u80fd\u8ba4\u5b9a\u8d44\u6599\u6536\u96c6\u7cfb\u7edf - \u5bc6\u7801\u4fee\u6539\u901a\u77e5', recipients=[current_user.email], body=body)
                    mail.send(msg_obj)
                    flash(f'\u5bc6\u7801\u5df2\u4fee\u6539\uff0c\u901a\u77e5\u5df2\u53d1\u5f80 {mask_email(current_user.email)}', 'success')
                except Exception as e:
                    current_app.logger.error(f'\u5bc6\u7801\u4fee\u6539\u901a\u77e5\u90ae\u4ef6\u53d1\u9001\u5931\u8d25: {e}')
                    flash('\u5bc6\u7801\u5df2\u4fee\u6539\uff0c\u4f46\u901a\u77e5\u90ae\u4ef6\u53d1\u9001\u5931\u8d25', 'warning')
                return redirect(url_for('super_admin.theme_settings'))
    return render_template('super_admin_account.html')


# 超管修改密码接口（用于站点设置中的邮箱保存）
@super_admin_bp.route('/super_admin/change_password', methods=['POST'])
@login_required
@role_required('super_admin')
def super_admin_change_password():
    new_pw = request.form.get('new_password', '')
    confirm = request.form.get('confirm_password', '')
    if not current_user.email or not current_user.email_verified:
        flash('邮箱未配置，修改密码后无法收到留底通知。', 'warning')
    if new_pw != confirm:
        flash('两次密码不一致', 'danger')
    else:
        valid, msg = validate_strong_password(new_pw)
        if not valid:
            flash(msg, 'danger')
        else:
            current_user.password_hash = generate_password_hash(new_pw)
            db.session.commit()
            flash('密码已修改', 'success')
    return redirect(url_for('super_admin.theme_settings'))
