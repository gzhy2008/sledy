"""班主任路由 Blueprint（含班级操作）"""
import os
import re
import time
import random
import openpyxl
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, jsonify, current_app
)
from flask_login import login_required, current_user
from flask_mail import Message
from werkzeug.security import generate_password_hash

from sqlalchemy.orm import joinedload
from models import db, User, UserProfile, ClassGroup, HeadTeacher, ExamBatch, Student, AdminProfile, Department, batch_classes
from utils import validate_id_number_checksum, role_required
from services import validate_phone, generate_random_password
from shared import (
    validate_image, validate_other_file, validate_photo,
    save_file, revoke_student_registrations_and_notify,
    get_admin_department_ids, check_rate_limit, _login_attempts,
    notify_student, audit_log, send_credentials_notification
)

teacher_bp = Blueprint('teacher', __name__)


#-----------班主任账户页面
@teacher_bp.route('/teacher/account')
@login_required
@role_required('headteacher')
def teacher_account():
    departments = Department.query.all()   # 获取所有系部供多选
    return render_template('teacher_account.html', departments=departments)

#------------重设班主任密码
@teacher_bp.route('/admin/reset_teacher_password/<int:teacher_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def reset_teacher_password(teacher_id):
    ht = HeadTeacher.query.get_or_404(teacher_id)
    new_pwd = generate_random_password()
    if not ht.user:
        flash('未找到关联用户', 'danger')
        return redirect(url_for('admin.manage_teachers'))
    ht.user.password_hash = generate_password_hash(new_pwd)
    db.session.commit()
    audit_log('RESET_TEACHER_PASSWORD', f'{ht.name}({ht.user.username})')
    # 将新凭证邮件发送给操作者留底
    email_ok, email_msg = send_credentials_notification(current_user, ht.name, ht.user.username, 'headteacher', new_pwd, '重置')
    flash(f'已重置班主任 {ht.name} 的密码为 {new_pwd}', 'success')
    if not email_ok:
        flash(email_msg, 'warning')
    return redirect(url_for('admin.manage_teachers'))

#-----------------
@teacher_bp.route('/teacher/profile', methods=['GET', 'POST'])
@login_required
@role_required('headteacher')
def teacher_profile():
    ht = current_user.head_teacher
    departments = Department.query.all()
    if request.method == 'POST':
        # 修改密码
        new_password = request.form.get('password')
        if new_password:
            valid, msg = validate_password_strength(new_password)
            if not valid:
                flash(msg, 'danger')
            else:
                current_user.password_hash = generate_password_hash(new_password)
                flash('密码修改成功', 'success')

        # 邮箱验证（使用 User 表的 email_verified 字段）
        email = request.form.get('email')
        if email and not current_user.email_verified:
            code = request.form.get('email_code')
            if not code:
                flash('请输入邮箱验证码', 'danger')
            elif code != session.get('email_verify_code'):
                flash('验证码错误', 'danger')
            elif time.time() - session.get('email_verify_code_time', 0) > 300:
                flash('验证码已过期，请重新获取', 'danger')
            else:
                current_user.email = email
                current_user.email_verified = True
                flash('邮箱验证成功', 'success')
                session.pop('email_verify_code', None)
                session.pop('email_verify_code_time', None)

        # 手机号（保留兼容）
        phone = request.form.get('phone')
        if phone:
            ht.phone = phone

        # 系部多选更新
        department_ids = request.form.getlist('department_ids')
        if department_ids:
            ht.departments = Department.query.filter(Department.id.in_(department_ids)).all()
        else:
            ht.departments = []

        db.session.commit()
        return redirect(url_for('teacher.teacher_profile'))

    return render_template('teacher_profile.html', ht=ht, departments=departments)

#-----------------------
@teacher_bp.route('/teacher/update_department', methods=['POST'])
@login_required
@role_required('headteacher')
def teacher_update_department():
    department_ids = request.form.getlist('department_ids')
    ht = current_user.head_teacher
    if department_ids:
        ht.departments = Department.query.filter(Department.id.in_(department_ids)).all()
    else:
        ht.departments = []
    db.session.commit()
    return jsonify({'status': 'success'})

@teacher_bp.route('/teacher/update_phone', methods=['POST'])
@login_required
@role_required('headteacher')
def teacher_update_phone():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return jsonify({'status': 'error', 'msg': '手机号不能为空'})
    if not validate_phone(phone):
        return jsonify({'status': 'error', 'msg': '手机号格式不正确'})

    # 全局唯一性检查：排除自己
    existing_teacher = HeadTeacher.query.filter(
        HeadTeacher.phone == phone,
        HeadTeacher.user_id != current_user.id
    ).first()
    existing_admin = AdminProfile.query.filter(AdminProfile.phone == phone).first()
    existing_student = UserProfile.query.filter(UserProfile.phone == phone).first()

    if existing_teacher:
        return jsonify({'status': 'error', 'msg': '该手机号已被其他班主任使用'})
    if existing_admin:
        return jsonify({'status': 'error', 'msg': '该手机号已被某位业务管理员使用'})
    if existing_student:
        return jsonify({'status': 'error', 'msg': '该手机号已被某位学生使用'})

    current_user.head_teacher.phone = phone
    db.session.commit()
    return jsonify({'status': 'success'})

@teacher_bp.route('/teacher/reset_email', methods=['POST'])
@login_required
@role_required('headteacher')
def teacher_reset_email():
    current_user.email_verified = False
    db.session.commit()
    return jsonify({'status': 'success'})
#-----------------------
@teacher_bp.route('/teacher/classes')
@login_required
@role_required('headteacher')
def teacher_classes():
    classes = current_user.head_teacher.classes
    # 批量统计每个班级的已审核和总学生数
    class_stats = {}
    for c in classes:
        total = c.students.count()
        approved = c.students.filter_by(status='approved').count()
        class_stats[c.id] = (approved, total)
    return render_template('teacher_classes.html', classes=classes, class_stats=class_stats)

#----------------
@teacher_bp.route('/teacher/class/create', methods=['GET', 'POST'])
@login_required
@role_required('headteacher')
def teacher_create_class():
    departments = current_user.head_teacher.departments
    if not departments:
        flash('您尚未选择所属系部，请先在账户管理中设置系部', 'warning')
        return redirect(url_for('teacher.teacher_account'))

    if request.method == 'POST':
        action = request.form.get('action', 'create')   # 新增：获取按钮动作
        class_name = request.form.get('class_name', '').strip()
        class_no = request.form.get('class_no', '').strip()
        department_id = request.form.get('department_id')

        errors = []
        if not class_name: errors.append('班级名称不能为空')
        if not class_no: errors.append('班级号不能为空')
        if not re.match(r'^[A-Z0-9]+$', class_no):
            errors.append('班级号只能包含大写英文字母和数字')
        if ClassGroup.query.filter_by(name=class_name).first():
            errors.append('班级名称已存在')
        if ClassGroup.query.filter_by(class_no=class_no).first():
            errors.append('班级号已存在')
        if not department_id:
            errors.append('请选择系部')
        elif int(department_id) not in [d.id for d in departments]:
            errors.append('无效的系部选择')

        if errors:
            for e in errors: flash(e, 'danger')
            return render_template('teacher_create_class.html', departments=departments)

        new_class = ClassGroup(
            name=class_name,
            class_no=class_no,
            department_id=int(department_id),
            teacher_id=current_user.head_teacher.id,
            created_by=current_user.id
        )
        db.session.add(new_class)
        db.session.commit()

        flash(f'班级 {class_name} 创建成功，您已成为该班班主任', 'success')
        if action == 'continue':
            return redirect(url_for('teacher.teacher_create_class'))
        else:
            return redirect(url_for('teacher.teacher_classes'))

    return render_template('teacher_create_class.html', departments=departments)

#------------------
@teacher_bp.route('/admin/handle_transfer/<int:profile_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def handle_transfer(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    if profile.transfer_status != 'pending':
        flash('该学生没有待处理的转班申请', 'warning')
        return redirect(request.referrer or url_for('admin.admin_profiles'))

    action = request.form.get('action')  # approve / reject
    if action not in ('approve', 'reject'):
        flash('无效操作', 'danger')
        return redirect(request.referrer or url_for('admin.admin_profiles'))

    # 权限检查：班主任只能处理目标班级是自己的转班申请
    if current_user.role == 'headteacher':
        allowed_ids = [c.id for c in current_user.head_teacher.classes]
        if profile.transfer_class_id not in allowed_ids:
            flash('无权处理该转班申请', 'danger')
            return redirect(url_for('teacher.teacher_profiles'))

    if action == 'approve':
        # 执行转班
        target_class = ClassGroup.query.get(profile.transfer_class_id)
        profile.class_id = target_class.id
        profile.department_name = target_class.department.name if target_class.department else ''
        profile.status = 'pending'
        profile.reject_reason = ''
        profile.transfer_status = 'approved'
        flash('转班申请已通过，档案已重置为待审核', 'success')
    else:
        profile.transfer_status = 'rejected'
        flash('转班申请已拒绝', 'success')

    db.session.commit()

    # 发送邮件通知
    dashboard_url = url_for('student.student_dashboard', _external=True)
    if action == 'approve':
        body = (
            f"同学 {profile.name}，您好！\n\n"
            f"您的转班申请已通过，已转入新班级。档案已重置为待审核状态，请等待新班主任审核。\n\n"
            f"请点击以下链接登录查看：\n"
            f"{dashboard_url}"
        )
        notify_student(profile.user, '技能认定资料收集系统 - 转班申请通过', body)
    elif action == 'reject':
        body = (
            f"同学 {profile.name}，您好！\n\n"
            f"您的转班申请未被通过。如有疑问，请联系班主任或管理员。\n\n"
            f"请点击以下链接登录查看：\n"
            f"{dashboard_url}"
        )
        notify_student(profile.user, '技能认定资料收集系统 - 转班申请结果', body)

    return redirect(request.referrer or url_for('admin.admin_profiles'))
#-------------------
@teacher_bp.route('/admin/class/<int:class_id>')
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def view_class(class_id):
    cls = ClassGroup.query.get_or_404(class_id)
    # 获取该班级的学生统计
    profile_count = UserProfile.query.filter_by(class_id=cls.id).count()
    return render_template('view_class.html', cls=cls, profile_count=profile_count)

#-------------------
@teacher_bp.route('/admin/class/<int:class_id>/toggle_active', methods=['POST'])
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def toggle_class_active(class_id):
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限进行此操作，仅限全局管理员', 'danger')
        return redirect(url_for('admin.manage_classes'))

    cls = ClassGroup.query.get_or_404(class_id)
    if current_user.role == 'headteacher':
        if cls.teacher_id != current_user.head_teacher.id:
            flash('无权操作此班级', 'danger')
            return redirect(request.referrer or url_for('teacher.teacher_classes'))
    if cls.is_graduated:
        flash('已毕业的班级无法修改活跃状态', 'danger')
        return redirect(request.referrer or url_for('teacher.teacher_classes'))
    cls.is_active = not cls.is_active
    status = '启用' if cls.is_active else '停用'
    db.session.commit()
    flash(f'班级 {cls.name} 已{status}', 'success')
    return redirect(request.referrer or url_for('teacher.teacher_classes'))

@teacher_bp.route('/admin/class/<int:class_id>/graduate', methods=['POST'])
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def graduate_class(class_id):
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限进行此操作，仅限全局管理员', 'danger')
        return redirect(url_for('admin.manage_classes'))

    cls = ClassGroup.query.get_or_404(class_id)
    if current_user.role == 'headteacher':
        if cls.teacher_id != current_user.head_teacher.id:
            flash('无权操作此班级', 'danger')
            return redirect(request.referrer or url_for('teacher.teacher_classes'))
    if cls.is_graduated:
        flash('该班级已经毕业', 'warning')
        return redirect(request.referrer or url_for('teacher.teacher_classes'))
    cls.is_graduated = True
    cls.is_active = False
    db.session.commit()
    flash(f'班级 {cls.name} 已标记为毕业（不可逆）', 'success')
    return redirect(request.referrer or url_for('teacher.teacher_classes'))
#------------------------------
@teacher_bp.route('/admin/class/<int:class_id>/assign_teacher', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def assign_class_teacher(class_id):
    cls = ClassGroup.query.get_or_404(class_id)
    teacher_id = request.form.get('teacher_id', '').strip()
    
    if not teacher_id:
        flash('请选择班主任', 'danger')
        return redirect(url_for('admin.manage_classes', tab='unassigned'))
    
    teacher = HeadTeacher.query.get(int(teacher_id))
    if not teacher:
        flash('班主任不存在', 'danger')
        return redirect(url_for('admin.manage_classes', tab='unassigned'))
    
    cls.teacher_id = teacher.id
    
    # 同步更新班主任的系部关联
    if cls.department not in teacher.departments:
        teacher.departments.append(cls.department)
    
    db.session.commit()
    flash(f'已为班级 {cls.name} 分配班主任 {teacher.name}', 'success')
    return redirect(url_for('admin.manage_classes', tab='unassigned'))

#------------------------------
@teacher_bp.route('/teacher/profiles')
@login_required
@role_required('headteacher')
def teacher_profiles():
    status = request.args.get('status', 'pending')
    class_id = request.args.get('class_id', type=int)
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    all_class_ids = [c.id for c in current_user.head_teacher.classes]
    if not all_class_ids:
        flash('您还没有关联任何班级', 'warning')
        return render_template('teacher_profiles.html', profiles=[], classes=[], current_status='pending',
                               current_class_id=None, teacher_class_ids=[], page=1, total_pages=0, total=0,
                               search='', status_counts={})

    query = UserProfile.query

    # 必须属于本班主任班级
    query = query.filter(UserProfile.class_id.in_(all_class_ids))
    if status != 'all':
        query = query.filter(UserProfile.status == status)
    if class_id and class_id in all_class_ids:
        query = query.filter(UserProfile.class_id == class_id)
    if search:
        query = query.filter(UserProfile.name.contains(search))

    # 各状态计数（基于本班主任管辖的所有班级）
    base_query = UserProfile.query.filter(UserProfile.class_id.in_(all_class_ids))
    status_counts = dict(
        db.session.query(UserProfile.status, db.func.count(UserProfile.id))
        .select_from(base_query.subquery()).group_by(UserProfile.status).all()
    )

    # 始终分页
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages

    profiles = query.order_by(UserProfile.id.desc()).limit(per_page).offset((page - 1) * per_page).all()
    classes = current_user.head_teacher.classes
    teacher_class_ids = [c.id for c in classes]

    return render_template('teacher_profiles.html',
                           profiles=profiles,
                           classes=classes,
                           current_status=status,
                           current_class_id=class_id,
                           teacher_class_ids=teacher_class_ids,
                           search=search,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           status_counts=status_counts)

#-----------------------------
@teacher_bp.route('/teacher/transfer_reviews')
@login_required
@role_required('headteacher')
def teacher_transfer_reviews():
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    all_class_ids = [c.id for c in current_user.head_teacher.classes]
    if not all_class_ids:
        flash('您还没有关联任何班级', 'warning')
        return render_template('teacher_transfer_reviews.html', profiles=[], teacher_class_ids=[], page=1, total_pages=0, total=0, search='')

    query = UserProfile.query.filter(
        UserProfile.transfer_status == 'pending',
        UserProfile.transfer_class_id.in_(all_class_ids)
    )
    if search:
        query = query.filter(UserProfile.name.contains(search))

    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages

    profiles = query.order_by(UserProfile.id.desc()).limit(per_page).offset((page - 1) * per_page).all()
    teacher_class_ids = all_class_ids

    return render_template('teacher_transfer_reviews.html',
                           profiles=profiles,
                           teacher_class_ids=teacher_class_ids,
                           search=search,
                           page=page,
                           total_pages=total_pages,
                           total=total)

#-----------------------------
@teacher_bp.route('/teacher/profile/review/<int:profile_id>')
@login_required
@role_required('headteacher')
def teacher_review_profile(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    allowed_ids = [c.id for c in current_user.head_teacher.classes]
    if profile.class_id not in allowed_ids:
        flash('无权查看该学生档案', 'danger')
        return redirect(url_for('teacher.teacher_profiles'))

    photo_url = edu_url = front_url = back_url = ''
    if profile.photo_path:
        photo_url = url_for('uploaded_file', filename=os.path.basename(profile.photo_path))
    if profile.edu_cert_path:
        edu_url = url_for('uploaded_file', filename=os.path.basename(profile.edu_cert_path))
    if profile.id_card_front_path:
        front_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_front_path))
    if profile.id_card_back_path:
        back_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_back_path))

    return render_template('admin_profile_review.html',
                           profile=profile,
                           photo_url=photo_url,
                           edu_url=edu_url,
                           id_card_front_url=front_url,
                           id_card_back_url=back_url)

@teacher_bp.route('/teacher/profile/approve/<int:profile_id>', methods=['POST'])
@login_required
@role_required('headteacher')
def teacher_approve_profile(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    allowed_ids = [c.id for c in current_user.head_teacher.classes]
    if profile.class_id not in allowed_ids:
        flash('无权审核非本班学生档案', 'danger')
        return redirect(url_for('teacher.teacher_profiles'))

    action = request.form.get('action')
    if action not in ('approve', 'reject'):
        flash('无效操作', 'danger')
        return redirect(url_for('teacher.teacher_profiles'))

    if action == 'reject':
        reason = request.form.get('reason', '').strip()
        if len(reason) < 5 or len(reason) > 20:
            flash('拒绝原因需5-20字', 'danger')
            return redirect(url_for('teacher.teacher_profiles'))
        profile.reject_reason = reason
        profile.status = 'rejected'
    else:
        profile.status = 'approved'
        profile.reject_reason = ''

    db.session.commit()

    # 发送邮件通知
    dashboard_url = url_for('student.student_dashboard', _external=True)
    if action == 'approve':
        body = (
            f"同学 {profile.name}，您好！\n\n"
            f"您的个人档案已由班主任审核通过，现在可以报名参加技能认定了。\n\n"
            f"请点击以下链接登录查看：\n"
            f"{dashboard_url}"
        )
        notify_student(profile.user, '技能认定资料收集系统 - 档案审核通过', body)
    elif action == 'reject':
        body = (
            f"同学 {profile.name}，您好！\n\n"
            f"您的个人档案审核未通过。\n"
            f"原因：{profile.reject_reason}\n\n"
            f"请登录系统修改后重新提交：\n"
            f"{dashboard_url}"
        )
        notify_student(profile.user, '技能认定资料收集系统 - 档案审核结果', body)

    flash(f'档案审核已{profile.status}', 'success')
    return redirect(url_for('teacher.teacher_profiles'))
#----------------
@teacher_bp.route('/teacher/class/<int:class_id>/batches')
@login_required
@role_required('headteacher')
def teacher_class_batches(class_id):
    # 验证班级属于当前班主任
    cls = ClassGroup.query.get_or_404(class_id)
    if cls.teacher_id != current_user.head_teacher.id:
        flash('无权查看此班级', 'danger')
        return redirect(url_for('teacher.teacher_classes'))

    status = request.args.get('status', 'active')   # 默认进行中（未归档）
    now = datetime.now()

    # 该班级关联的批次
    batches_query = cls.batches

    if status == 'active':
        batches_query = [b for b in batches_query if not b.is_archived]
    elif status == 'archived':
        batches_query = [b for b in batches_query if b.is_archived]
    # 'all' 保持全部

    # 手动排序：进行中的按开始时间倒序，已归档的按归档时间倒序？简单处理按创建时间倒序
    batches_query = sorted(batches_query, key=lambda b: b.created_at, reverse=True)

    # 分页（每页50条）
    per_page = 50
    page = request.args.get('page', 1, type=int)
    total = len(batches_query)
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages
    batches = batches_query[(page-1)*per_page : page*per_page]

    return render_template('teacher_class_batches.html',
                           cls=cls,
                           batches=batches,
                           current_status=status,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           now=now)

#----------------
@teacher_bp.route('/teacher/batch/<batch_name>/compare_ids', methods=['POST'])
@login_required
@role_required('headteacher')
def teacher_batch_compare_ids(batch_name):
    batch_obj = ExamBatch.query.filter_by(batch_name=batch_name).first_or_404()
    # 验证该批次是否关联到班主任管理的班级
    teacher_class_ids = [c.id for c in current_user.head_teacher.classes]
    batch_class_ids = [c.id for c in batch_obj.classes]
    if not any(cid in teacher_class_ids for cid in batch_class_ids):
        flash('您无权比对非本班关联的批次', 'danger')
        return redirect(url_for('teacher.teacher_profiles'))

    if 'file' not in request.files:
        flash('未选择文件', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))
    file = request.files['file']
    if file.filename == '':
        flash('未选择文件', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    # 检查文件大小（512KB）
    file.seek(0, os.SEEK_END)
    file_size = file.tell()
    file.seek(0)
    if file_size > 512 * 1024:
        flash('文件大小不能超过512KB', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    # 检查文件扩展名
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ('xlsx', 'xls'):
        flash('仅支持 .xlsx 或 .xls 格式的 Excel 文件', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    try:
        wb = openpyxl.load_workbook(file)
        ws = wb.active
    except Exception as e:
        flash(f'无法读取 Excel 文件: {str(e)}', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    # 逐行读取并核验
    excel_data = []  # [(name, id_number), ...]
    id_set = set()
    errors = []
    for row_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or (not row[0] and not row[1]):
            continue
        name = str(row[0]).strip() if row[0] else ''
        id_number = str(row[1]).strip() if row[1] else ''
        if not name:
            errors.append(f'第 {row_idx} 行姓名为空')
            continue
        if not id_number:
            errors.append(f'第 {row_idx} 行身份证号为空')
            continue
        if not validate_id_number_checksum(id_number):
            errors.append(f'第 {row_idx} 行身份证号格式或校验码不正确')
            continue
        if id_number in id_set:
            errors.append(f'第 {row_idx} 行身份证号与文件中其他行重复')
            continue
        id_set.add(id_number)
        excel_data.append((name, id_number))

    if errors:
        for err in errors:
            flash(err, 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    if not excel_data:
        flash('Excel 文件中未找到有效数据', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    # Excel中的身份证号集合
    excel_ids = {data[1] for data in excel_data}

    # 获取本班所有学生
    teacher_class_ids = [c.id for c in current_user.head_teacher.classes]
    class_students = UserProfile.query.filter(UserProfile.class_id.in_(teacher_class_ids)).all()
    class_student_ids = {s.id_number: s.name for s in class_students}

    # 本班在该批次已报名的学生（不论状态）
    registered_in_class = Student.query.filter(
        Student.batch_id == batch_obj.id,
        Student.id_number.in_(class_student_ids.keys())
    ).all()

    # 整个批次已报名的学生（不论班级、不论状态）
    all_registered_ids = {s.id_number for s in Student.query.filter(Student.batch_id == batch_obj.id).all()}

    # 统计
    total_excel = len(excel_data)
    total_registered = len([s for s in registered_in_class if s.status == 'approved'])

    # 三个分类
    approved_and_in_excel = []      # 系统已通过，Excel有
    approved_not_in_excel = []      # 系统已通过，Excel无
    in_excel_not_registered = []    # Excel有，系统中无报名记录
    in_excel_not_approved = []      # Excel有，系统报名但未通过

    # 系统已通过的学生
    approved_students = {s.id_number: s for s in registered_in_class if s.status == 'approved'}

    for id_num, s in approved_students.items():
        if id_num in excel_ids:
            approved_and_in_excel.append((s.name, id_num))
        else:
            approved_not_in_excel.append((s.name, id_num))

    for name, id_num in excel_data:
        if id_num not in all_registered_ids:
            in_excel_not_registered.append((name, id_num))
        elif id_num not in approved_students:
            in_excel_not_approved.append((name, id_num))

    return render_template('batch_compare_result.html',
                           batch=batch_obj,
                           total_excel=total_excel,
                           total_registered=total_registered,
                           approved_and_in_excel=approved_and_in_excel,
                           approved_not_in_excel=approved_not_in_excel,
                           in_excel_not_registered=in_excel_not_registered,
                           in_excel_not_approved=in_excel_not_approved)

#----------------           
@teacher_bp.route('/teacher/reset_student_profile/<int:profile_id>', methods=['POST'])
@login_required
@role_required('headteacher')
def teacher_reset_student_profile(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    allowed_ids = [c.id for c in current_user.head_teacher.classes]
    if profile.class_id not in allowed_ids:
        flash('无权操作非本班学生档案', 'danger')
        return redirect(url_for('teacher.teacher_profiles'))

    if profile.status == 'pending':
        flash('档案当前即为待审核状态', 'warning')
        return redirect(request.referrer or url_for('teacher.teacher_profiles'))

    revoked = revoke_student_registrations_and_notify(profile)

    profile.status = 'pending'
    profile.reject_reason = ''
    if revoked:
        profile.user.reset_note = f"您的档案已被班主任重置为待审核，以下批次报名已被撤销：{', '.join(revoked)}。请重新编辑档案并提交。"
    else:
        profile.user.reset_note = "您的档案已被班主任重置为待审核，请重新编辑档案并提交。"
    db.session.commit()

    flash(f'已重置学生 {profile.name} 的档案状态为待审核，通知已发送。', 'success')
    return redirect(request.referrer or url_for('teacher.teacher_profiles'))

