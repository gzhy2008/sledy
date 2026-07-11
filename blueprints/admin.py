"""业务管理员路由 Blueprint"""
import os
import re
import time
import random
import string
import shutil
import tempfile
import openpyxl
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, jsonify, current_app, send_file
)
from flask_login import login_required, current_user
from flask_mail import Message
from werkzeug.security import generate_password_hash

from models import db, User, UserProfile, ClassGroup, HeadTeacher, ExamBatch, Student, AdminProfile, Department, batch_classes, Skill, Notice
from sqlalchemy.orm import joinedload
from sqlalchemy import case
from utils import validate_id_number_checksum, role_required
from services import validate_phone, ROLE_NAMES, generate_random_password, mask_email
from shared import (
    validate_image, validate_other_file, validate_pdf_or_image,
    save_file, send_verify_code, revoke_student_registrations_and_notify,
    get_admin_department_ids, send_credentials_notification, audit_log,
    check_rate_limit, _login_attempts, notify_student,
    notify_class_students
)

admin_bp = Blueprint('admin', __name__)


#----- 业务管理员账户页面  --------
@admin_bp.route('/admin/account')
@login_required
@role_required('admin')
def admin_account():
    return render_template('admin_account.html')
#-----------------------
@admin_bp.route('/admin/classes')
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def manage_classes():
    tab = request.args.get('tab', 'all')
    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    admin_dept_ids = get_admin_department_ids()
    
    query = ClassGroup.query.options(
        joinedload(ClassGroup.department),
        joinedload(ClassGroup.teacher)
    )
    
    # 权限过滤：普通管理员只能看自己管辖系部的班级
    if admin_dept_ids is not None:
        query = query.filter(ClassGroup.department_id.in_(admin_dept_ids))
    
    # 状态选项卡过滤
    if tab == 'active':
        query = query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False)
    elif tab == 'inactive':
        query = query.filter(ClassGroup.is_active == False)
    elif tab == 'graduated':
        query = query.filter(ClassGroup.is_graduated == True)
    elif tab == 'unassigned':
        query = query.filter(ClassGroup.teacher_id == None, ClassGroup.is_active == True, ClassGroup.is_graduated == False)
    else:  # all
        pass  # 显示所有班级
    
    # 搜索
    if search:
        query = query.filter(ClassGroup.name.contains(search) | ClassGroup.class_no.contains(search))
    
    # 分页
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages
    classes = query.order_by(ClassGroup.name).limit(per_page).offset((page - 1) * per_page).all()
    
    # 获取可分配的班主任
    teacher_query = HeadTeacher.query.join(User).filter(User.is_active == True)
    if admin_dept_ids is not None:
        teacher_query = teacher_query.filter(HeadTeacher.departments.any(Department.id.in_(admin_dept_ids)))
    teachers = teacher_query.all()
    
    # 统计各状态数量
    counts = {}
    base_query = ClassGroup.query
    if admin_dept_ids is not None:
        base_query = base_query.filter(ClassGroup.department_id.in_(admin_dept_ids))
    counts['all'] = base_query.count()
    counts['active'] = base_query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False).count()
    counts['inactive'] = base_query.filter(ClassGroup.is_active == False).count()
    counts['graduated'] = base_query.filter(ClassGroup.is_graduated == True).count()
    counts['unassigned'] = base_query.filter(ClassGroup.teacher_id == None, ClassGroup.is_active == True, ClassGroup.is_graduated == False).count()
    
    return render_template('manage_classes.html',
                           classes=classes,
                           teachers=teachers,
                           current_tab=tab,
                           search=search,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           counts=counts)

#----------------------
@admin_bp.route('/admin/classes/create', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def create_class():
    if current_user.role == 'admin':
        flash('班级创建交由班主任负责，业务管理员无此权限', 'danger')
        return redirect(url_for('admin.manage_classes'))

    teachers = HeadTeacher.query.all()

    # 根据管理员类型获取系部列表
    if current_user.role == 'admin':
        ap = current_user.admin_profile
        if ap and not ap.is_global:
            departments = ap.departments   # 普通管理员：只显示管辖系部
        else:
            departments = Department.query.all()
    else:
        departments = Department.query.all()

    if request.method == 'POST':
        class_name = request.form.get('class_name', '').strip()
        class_no = request.form.get('class_no', '').strip()
        teacher_id = request.form.get('teacher_id')
        department_id = request.form.get('department_id', type=int)

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
        elif department_id not in [d.id for d in departments]:
            errors.append('无效的系部选择')

        if errors:
            for e in errors: flash(e, 'danger')
            return render_template('create_class.html', teachers=teachers, departments=departments)

        new_class = ClassGroup(
            name=class_name,
            class_no=class_no,
            department_id=department_id,
            created_by=current_user.id
        )
        if teacher_id:
            new_class.teacher_id = int(teacher_id)
        db.session.add(new_class)
        db.session.commit()
        flash(f'班级 {class_name} 创建成功', 'success')
        return redirect(url_for('admin.manage_classes'))

    return render_template('create_class.html', teachers=teachers, departments=departments)

# ---- 系部管理 ----
@admin_bp.route('/admin/departments')
@login_required
@role_required('admin', 'super_admin')
def manage_departments():
    departments = Department.query.filter_by(is_deleted=False).order_by(Department.id).all()
    # 获取每个系部下的未毕业班级
    department_classes = {}
    for d in departments:
        department_classes[d.id] = ClassGroup.query.filter_by(department_id=d.id, is_graduated=False).all()
    return render_template('admin_departments.html', departments=departments, department_classes=department_classes)

#-----------------
@admin_bp.route('/admin/departments/create', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')
def create_department():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        action = request.form.get('action', 'create')   # 获取按钮动作

        if not name:
            flash('系部名称不能为空', 'danger')
        elif Department.query.filter_by(name=name).first():
            flash('系部已存在', 'danger')
        else:
            db.session.add(Department(name=name))
            db.session.commit()
            flash('系部创建成功', 'success')
            if action == 'continue':
                return redirect(url_for('admin.create_department'))
            else:
                return redirect(url_for('admin.manage_departments'))
    return render_template('department_form.html')

@admin_bp.route('/admin/departments/edit/<int:dept_id>', methods=['GET', 'POST'])
@login_required
@role_required('super_admin')   # 仅超管
def edit_department(dept_id):
    dept = Department.query.get_or_404(dept_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('系部名称不能为空', 'danger')
        elif Department.query.filter(Department.name == name, Department.id != dept_id).first():
            flash('系部名称重复', 'danger')
        else:
            dept.name = name
            db.session.commit()
            flash('系部更新成功', 'success')
            return redirect(url_for('admin.manage_departments'))
    return render_template('department_form.html', dept=dept)

#--------------------
@admin_bp.route('/admin/departments/delete/<int:dept_id>', methods=['POST'])
@login_required
@role_required('super_admin')
def delete_department(dept_id):
    dept = Department.query.get_or_404(dept_id)
    if dept.classes:
        flash('该系部下有班级，无法删除', 'danger')
    else:
        dept.is_deleted = True
        db.session.commit()
        flash('系部已标记为删除', 'success')
    return redirect(url_for('admin.manage_departments'))

# -------- 工种管理 --------
@admin_bp.route('/admin/skills')
@login_required
@role_required('admin', 'super_admin')
def manage_skills():
    search = request.args.get('search', '').strip()
    query = Skill.query
    if search:
        query = query.filter(Skill.name.contains(search))
    skills = query.order_by(Skill.name).all()
    return render_template('admin_skills.html', skills=skills, search=search)

@admin_bp.route('/admin/skills/create', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def create_skill():
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限进行新增或修改，仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        action = request.form.get('action', 'create')
        if not name:
            flash('工种名称不能为空', 'danger')
        elif Skill.query.filter_by(name=name).first():
            flash('工种已存在', 'danger')
        else:
            db.session.add(Skill(name=name))
            db.session.commit()
            flash('工种创建成功', 'success')
            if action == 'continue':
                return redirect(url_for('admin.create_skill'))
            return redirect(url_for('admin.manage_skills'))
    return render_template('skill_form.html')

@admin_bp.route('/admin/skills/edit/<int:skill_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def edit_skill(skill_id):
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限进行新增或修改，仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))
    
    skill = Skill.query.get_or_404(skill_id)
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            flash('工种名称不能为空', 'danger')
        elif Skill.query.filter(Skill.name == name, Skill.id != skill_id).first():
            flash('工种名称与已有工种重复', 'danger')
        elif skill.name == name:
            flash('工种名称未改变，无需修改', 'info')
        else:
            skill.name = name
            db.session.commit()
            flash('工种名称已更新', 'success')
            return redirect(url_for('admin.manage_skills'))
            return render_template('skill_form.html', skill=skill)

#------------------
@admin_bp.route('/admin/skills/toggle_lock/<int:skill_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def toggle_lock_skill(skill_id):
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限进行新增或修改，仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))
    
    skill = Skill.query.get_or_404(skill_id)
    skill.is_locked = not skill.is_locked
    status = '锁定' if skill.is_locked else '解锁'
    db.session.commit()
    flash(f'工种已{status}', 'success')
    return redirect(url_for('admin.manage_skills'))

# ---- 批次管理 ----
@admin_bp.route('/admin/batches')
@login_required
@role_required('admin', 'super_admin')
def manage_batches():
    status = request.args.get('status', 'active')
    search = request.args.get('search', '').strip()
    skill_name = request.args.get('skill_name', '').strip()
    keyword = request.args.get('keyword', '').strip()    # 批次简述搜索
    page = request.args.get('page', 1, type=int)
    per_page = 50
    now = datetime.now()

    query = ExamBatch.query.options(joinedload(ExamBatch.skill))

    if status == 'active':
        query = query.filter(ExamBatch.is_archived == False, ExamBatch.end_time >= now)
    elif status == 'expired':
        query = query.filter(ExamBatch.is_archived == False, ExamBatch.end_time < now)
    elif status == 'archived':
        query = query.filter(ExamBatch.is_archived == True)

    if search:
        query = query.filter(ExamBatch.batch_name.contains(search))

    if skill_name:
        query = query.filter(ExamBatch.skill.has(Skill.name == skill_name))

    if keyword:
        query = query.filter(ExamBatch.batch_keyword.contains(keyword))

    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages

    batches = query.order_by(ExamBatch.created_at.desc()).limit(per_page).offset((page - 1) * per_page).all()

    # 计算每个批次的报名统计
    batch_stats = {}
    for b in batches:
        pending_count = Student.query.filter_by(batch_id=b.id, status='pending').count()
        total_count = Student.query.filter_by(batch_id=b.id).count()
        batch_stats[b.id] = (pending_count, total_count)

    # 工种列表（用于下拉选择）
    skills = Skill.query.filter(Skill.is_locked == False).order_by(Skill.name).all()

    return render_template('admin_batches.html',
                           batches=batches,
                           batch_stats=batch_stats,
                           now=now,
                           current_status=status,
                           search=search,
                           skill_name=skill_name,
                           keyword=keyword,
                           skills=skills,
                           page=page,
                           total_pages=total_pages,
                           total=total)
#--------------------------

@admin_bp.route('/admin/batch/<batch_name>/compare_ids', methods=['POST'])
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def batch_compare_ids(batch_name):
    batch_obj = ExamBatch.query.filter_by(batch_name=batch_name).first_or_404()
    if 'file' not in request.files:
        flash('未选择文件', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    file = request.files['file']
    if file.filename == '':
        flash('未选择文件', 'danger')
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

    # 逐行读取姓名+身份证号，跳过标题行
    excel_data = []  # [(name, id_number), ...]
    id_set = set()
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or (not row[0] and not row[1]):
            continue
        name = str(row[0]).strip() if row[0] else ''
        id_number = str(row[1]).strip() if len(row) > 1 and row[1] else ''
        if not name or not id_number:
            continue
        if id_number in id_set:
            continue
        id_set.add(id_number)
        excel_data.append((name, id_number))

    if not excel_data:
        flash('Excel 文件中未找到有效数据', 'danger')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_name))

    excel_ids = {data[1] for data in excel_data}

    # 该批次所有已报名学生（不论班级、不论状态）
    all_registered = Student.query.filter(Student.batch_id == batch_obj.id).all()
    all_registered_ids = {s.id_number for s in all_registered}

    # 统计
    total_excel = len(excel_data)
    total_registered = len([s for s in all_registered if s.status == 'approved'])

    approved_and_in_excel = []      # 系统已通过，Excel有
    approved_not_in_excel = []      # 系统已通过，Excel无
    in_excel_not_registered = []    # Excel有，系统中无报名记录
    in_excel_not_approved = []      # Excel有，系统报名但未通过

    approved_students = {s.id_number: s for s in all_registered if s.status == 'approved'}

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

#-------------------------
@admin_bp.route('/admin/batch/<int:batch_id>/edit_classes', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def edit_batch_classes(batch_id):
    batch = ExamBatch.query.get_or_404(batch_id)
    readonly = batch.is_archived or batch.is_locked or batch.end_time < datetime.now()

    # 获取当前管理员可管辖的系部ID列表
    admin_dept_ids = get_admin_department_ids()
    department_id = request.args.get('department_id', type=int)

    if request.method == 'POST':
        if readonly:
            flash('批次已归档、锁定或已过期，无法修改班级', 'danger')
            return redirect(url_for('admin.manage_batches'))

        action = request.form.get('action')
        class_id = request.form.get('class_id', type=int)

        if action == 'add':
            cls = ClassGroup.query.get(class_id)
            if cls and cls not in batch.classes:
                # 校验：普通管理员只能添加自己管辖系部下的班级
                if admin_dept_ids is not None and cls.department_id not in admin_dept_ids:
                    flash('您没有权限操作该系部的班级', 'danger')
                else:
                    batch.classes.append(cls)
                    db.session.commit()
                    notify_class_students(cls, batch, 'add')
                    flash(f'已添加班级 {cls.name}', 'success')
            else:
                flash('班级无效或已存在', 'danger')

        elif action == 'remove':
            cls = ClassGroup.query.get(class_id)
            if cls and cls in batch.classes:
                # 校验：普通管理员只能移除自己管辖系部下的班级
                if admin_dept_ids is not None and cls.department_id not in admin_dept_ids:
                    flash('您没有权限操作该系部的班级', 'danger')
                else:
                    student_count = Student.query.filter_by(batch_id=batch.id).join(
                        UserProfile, UserProfile.user_id == Student.user_id
                    ).filter(UserProfile.class_id == cls.id).count()
                    if student_count > 0:
                        flash(f'班级 {cls.name} 已有 {student_count} 名学生报名该批次，无法移除', 'danger')
                    else:
                        batch.classes.remove(cls)
                        db.session.commit()
                        notify_class_students(cls, batch, 'remove')
                        flash(f'已移除班级 {cls.name}', 'success')
            else:
                flash('班级无效或未关联', 'danger')

        return redirect(url_for('admin.edit_batch_classes', batch_id=batch.id, department_id=department_id))

    linked_classes = batch.classes

    # 根据管理员权限筛选可添加的班级
    class_query = ClassGroup.query.filter(
        ClassGroup.is_active == True,
        ClassGroup.is_graduated == False
    )

    # 普通管理员：只显示管辖系部下的班级
    if admin_dept_ids is not None:
        class_query = class_query.filter(ClassGroup.department_id.in_(admin_dept_ids))

    # 按系部筛选（可选）
    if department_id:
        # 校验：普通管理员不能选择非管辖系部
        if admin_dept_ids is not None and department_id not in admin_dept_ids:
            department_id = None
        else:
            class_query = class_query.filter(ClassGroup.department_id == department_id)

    all_classes = class_query.order_by(ClassGroup.name).all()
    unlinked_classes = [c for c in all_classes if c not in linked_classes]

    # 获取可供筛选的系部列表（仅显示管理员可管辖的系部）
    if admin_dept_ids is not None:
        departments = Department.query.filter(Department.id.in_(admin_dept_ids),Department.is_deleted == False).order_by(Department.name).all()
    else:
        departments = Department.query.filter_by(is_deleted=False).order_by(Department.name).all()

    return render_template('edit_batch_classes.html',
                           batch=batch,
                           linked_classes=linked_classes,
                           unlinked_classes=unlinked_classes,
                           departments=departments,
                           department_id=department_id,
                           readonly=readonly)

#-------------------------
@admin_bp.route('/admin/batch/<int:batch_id>/update_end_time', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def update_batch_end_time(batch_id):
    batch = ExamBatch.query.get_or_404(batch_id)
    if batch.is_archived:
        flash('已归档的批次无法修改时间', 'danger')
        return redirect(url_for('admin.manage_batches'))
    
    new_start_str = request.form.get('start_time', '').strip()
    new_end_str = request.form.get('end_time', '').strip()
    new_work_start_str = request.form.get('work_start_time', '').strip()
    new_work_end_str = request.form.get('work_end_time', '').strip()
    
    if not new_start_str or not new_end_str:
        flash('请选择报名开始时间和报名结束时间', 'danger')
        return redirect(url_for('admin.manage_batches'))
    
    try:
        new_start = datetime.strptime(new_start_str, '%Y-%m-%dT%H:%M')
        new_end = datetime.strptime(new_end_str, '%Y-%m-%dT%H:%M')
        new_work_start = datetime.strptime(new_work_start_str, '%Y-%m-%dT%H:%M') if new_work_start_str else None
        new_work_end = datetime.strptime(new_work_end_str, '%Y-%m-%dT%H:%M') if new_work_end_str else None
    except ValueError:
        flash('时间格式不正确', 'danger')
        return redirect(url_for('admin.manage_batches'))
    
    now = datetime.now()
    
    # 校验：报名结束时间不得早于报名开始时间
    if new_end <= new_start:
        flash('报名结束时间必须晚于报名开始时间', 'danger')
        return redirect(url_for('admin.manage_batches'))
    
    # 校验：报名结束时间不得早于当前时间
    if new_end < now:
        flash('报名结束时间不能早于当前时间', 'danger')
        return redirect(url_for('admin.manage_batches'))
    
    # 校验：认定开始时间（如果填写）
    if new_work_start:
        if new_work_start <= new_end:
            flash('认定开始时间必须晚于报名结束时间', 'danger')
            return redirect(url_for('admin.manage_batches'))
    
    # 校验：认定结束时间（如果填写）
    if new_work_end:
        if new_work_start and new_work_end <= new_work_start:
            flash('认定结束时间必须晚于认定开始时间', 'danger')
            return redirect(url_for('admin.manage_batches'))
        elif not new_work_start:
            flash('请先填写认定开始时间', 'danger')
            return redirect(url_for('admin.manage_batches'))
    
    batch.start_time = new_start
    batch.end_time = new_end
    batch.work_start_time = new_work_start
    batch.work_end_time = new_work_end
    db.session.commit()
    flash('批次时间已更新', 'success')
    return redirect(url_for('admin.manage_batches'))

#------------------------
@admin_bp.route('/admin/batch/create', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def create_batch():
    if request.method == 'POST':
        skill_id = request.form.get('skill_id', '').strip()
        skill_level = request.form.get('skill_level', '').strip()
        start_time = request.form.get('start_time', '').strip()
        end_time = request.form.get('end_time', '').strip()
        work_start_time = request.form.get('work_start_time', '').strip()
        work_end_time = request.form.get('work_end_time', '').strip()
        batch_keyword = request.form.get('batch_keyword', '').strip()[:10]
        action = request.form.get('action', 'create')

        errors = []
        if not skill_id: errors.append('请选择工种')
        if not skill_level: errors.append('等级不能为空')
        if not start_time or not end_time: errors.append('报名起止时间不能为空')
        if not work_start_time or not work_end_time: errors.append('认定工作起止时间不能为空')
        try:
            start_dt = datetime.strptime(start_time, '%Y-%m-%dT%H:%M')
            end_dt = datetime.strptime(end_time, '%Y-%m-%dT%H:%M')
            work_start_dt = datetime.strptime(work_start_time, '%Y-%m-%dT%H:%M')
            work_end_dt = datetime.strptime(work_end_time, '%Y-%m-%dT%H:%M')
            if start_dt.date() < datetime.now().date():
                errors.append('报名开始时间不能早于今天')
            if start_dt >= end_dt:
                errors.append('报名开始时间必须早于报名截止时间')
            if work_start_dt >= work_end_dt:
                errors.append('认定开始时间必须早于认定结束时间')
            if work_start_dt < end_dt:
                errors.append('认定开始时间不能早于报名截止时间')
            if work_start_dt.date() < datetime.now().date():
                errors.append('认定开始时间不能早于今天')
        except ValueError:
            errors.append('时间格式错误')

        if errors:
            for e in errors: flash(e, 'danger')
            skills = Skill.query.filter(Skill.is_locked == False).all()
            classes = ClassGroup.query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False).all()
            return render_template('batch_form.html', form=request.form, skills=skills, classes=classes)

        # 生成批次号（行锁）
        skill = db.session.query(Skill).filter_by(id=int(skill_id)).with_for_update().first()
        if not skill:
            flash('无效工种', 'danger')
            return redirect(url_for('admin.create_batch'))

        new_issue = skill.issue_number + 1
        date_str = datetime.now().strftime('%Y%m%d')
        random_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
        batch_name = f"{date_str}{skill.id}{new_issue}{random_code}"
        retry = 0
        while ExamBatch.query.filter_by(batch_name=batch_name).first() and retry < 3:
            random_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
            batch_name = f"{date_str}{skill.id}{new_issue}{random_code}"
            retry += 1
        if retry == 3 and ExamBatch.query.filter_by(batch_name=batch_name).first():
            flash('批次号生成失败，请重试', 'danger')
            return redirect(url_for('admin.create_batch'))

        batch = ExamBatch(
            batch_name=batch_name,
            batch_keyword=batch_keyword,
            skill_id=int(skill_id),
            skill_level=skill_level,
            start_time=start_dt,
            end_time=end_dt,
            work_start_time=work_start_dt,
            work_end_time=work_end_dt,
            issue_number=new_issue
            )

        # 不在创建时绑定班级，班级绑定移至编辑班级功能中
        db.session.add(batch)
        skill.issue_number = new_issue
        db.session.commit()

        flash(f'批次创建成功！批次号：{batch_name}。如需添加班级，请到批次列表点击“匹配班级”。', 'success')
        if action == 'continue':
            return redirect(url_for('admin.create_batch'))
        else:
            return redirect(url_for('admin.manage_batches'))

    skills = Skill.query.filter(Skill.is_locked == False).all()
    classes = ClassGroup.query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False).all()
    return render_template('batch_form.html', skills=skills, classes=classes)

#-----------------------
@admin_bp.route('/admin/batch/<int:batch_id>/toggle_lock', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def toggle_batch_lock(batch_id):
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限进行此操作，仅限全局管理员', 'danger')
        return redirect(url_for('admin.manage_batches'))

    batch = ExamBatch.query.get_or_404(batch_id)
    if batch.is_archived:
        flash('已归档的批次无法锁定/解锁', 'danger')
        return redirect(url_for('admin.manage_batches'))

    batch.is_locked = not batch.is_locked
    status = '锁定' if batch.is_locked else '解锁'
    db.session.commit()
    flash(f'批次已{status}', 'success')
    return redirect(url_for('admin.manage_batches'))

@admin_bp.route('/admin/batch/<int:batch_id>/toggle_archive', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def toggle_batch_archive(batch_id):
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限进行此操作，仅限全局管理员', 'danger')
        return redirect(url_for('admin.manage_batches'))

    batch = ExamBatch.query.get_or_404(batch_id)
    now = datetime.now()
    if batch.is_archived:
        batch.is_archived = False
        batch.archived_at = None
        flash('批次已取消归档，恢复为进行中状态', 'success')
    else:
        if batch.end_time > now:
            batch.end_time = now
        batch.is_archived = True
        batch.archived_at = now
        flash('批次已归档', 'success')
    db.session.commit()
    return redirect(url_for('admin.manage_batches'))

# ---- 学生报名审核（业务管理员） ----
@admin_bp.route('/admin/dashboard')
@login_required
@role_required('admin', 'super_admin')
def admin_dashboard():
    status = request.args.get('status', 'pending')        # 默认待审核
    skill_name = request.args.get('skill_name', '').strip()
    batch_search = request.args.get('batch_search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    query = Student.query.join(ExamBatch).options(
        joinedload(Student.batch).joinedload(ExamBatch.skill)
    )

    dept_ids = get_admin_department_ids()
    if dept_ids is not None:
        query = query.join(ExamBatch.classes).filter(ClassGroup.department_id.in_(dept_ids))

    # 各状态计数（不受当前status筛选影响）
    count_query = Student.query.join(ExamBatch)
    if dept_ids is not None:
        count_query = count_query.join(ExamBatch.classes).filter(ClassGroup.department_id.in_(dept_ids))

    # 状态过滤
    if status != 'all':
        query = query.filter(Student.status == status)

    # 工种过滤
    if skill_name:
        query = query.filter(ExamBatch.skill.has(Skill.name == skill_name))

    # 批次关键词搜索
    if batch_search:
        query = query.filter(ExamBatch.batch_name.contains(batch_search))

    # 始终分页
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages

    students = query.order_by(Student.created_at.desc()).limit(per_page).offset((page - 1) * per_page).all()

    # 批次列表（用于搜索下拉）
    batches_orm = ExamBatch.query.options(joinedload(ExamBatch.skill)).order_by(ExamBatch.created_at.desc()).all()
    batches_serializable = [{
        'batch_name': b.batch_name,
        'skill_name': b.skill.name if b.skill else '',
        'skill_level': b.skill_level,
        'display_title': b.display_title
    } for b in batches_orm]

    # 工种列表
    skills = Skill.query.filter(Skill.is_locked == False).order_by(Skill.name).all()

    # 各状态计数（基于当前管理员管辖范围）
    status_counts = dict(
        db.session.query(Student.status, db.func.count(Student.id))
        .select_from(Student).join(ExamBatch)
        .join(ExamBatch.classes).filter(ClassGroup.department_id.in_(dept_ids))
        .group_by(Student.status).all()
    ) if dept_ids is not None else dict(
        db.session.query(Student.status, db.func.count(Student.id))
        .group_by(Student.status).all()
    )

    return render_template('admin_dashboard.html',
                           students=students,
                           batches=batches_serializable,
                           skills=skills,
                           current_status=status,
                           skill_name=skill_name,
                           batch_search=batch_search,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           status_counts=status_counts)

#---------------
@admin_bp.route('/admin/batch/<batch_name>/reviews')
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def batch_reviews(batch_name):
    batch_obj = ExamBatch.query.filter_by(batch_name=batch_name).first_or_404()
    class_id = request.args.get('class_id', type=int)

    if current_user.role == 'headteacher':
        default_status = 'all'
    else:
        default_status = 'pending'
    status = request.args.get('status', default_status)
    page = request.args.get('page', 1, type=int)
    per_page = 50

    query = Student.query.filter_by(batch_id=batch_obj.id).options(
        joinedload(Student.batch).joinedload(ExamBatch.skill)
    )

    if class_id:
        query = query.join(UserProfile, UserProfile.user_id == Student.user_id)\
                     .filter(UserProfile.class_id == class_id)

    if status != 'all':
        query = query.filter(Student.status == status)

    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages

    if status == 'all':
        order_expression = case(
            (Student.status == 'pending', 1),
            else_=2
        ).asc()
        query = query.order_by(order_expression, Student.created_at.desc())
    else:
        query = query.order_by(Student.created_at.desc())

    students = query.limit(per_page).offset((page - 1) * per_page).all()

    # 获取当前管理员的管辖系部ID列表，用于班级链接权限判断
    admin_dept_ids = get_admin_department_ids()

    # 各状态数量统计
    status_counts = dict(
        db.session.query(Student.status, db.func.count(Student.id))
        .filter(Student.batch_id == batch_obj.id)
        .group_by(Student.status).all()
    )

    return render_template('batch_reviews.html',
                           batch=batch_obj,
                           students=students,
                           current_status=status,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           class_id=class_id,
                           admin_dept_ids=admin_dept_ids,
                           status_counts=status_counts)
                           
#-------------------------
@admin_bp.route('/admin/approve', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def approve_students():
    ids = request.form.getlist('student_ids')
    action = request.form.get('action')
    batch = request.args.get('batch', '')
    if not ids:
        flash('未选择任何学生', 'warning')
        return redirect(url_for('admin.admin_dashboard', batch=batch))
    new_status = 'approved' if action == 'approve' else 'rejected'
    Student.query.filter(Student.id.in_(ids)).update(
        {'status': new_status}, synchronize_session=False
    )
    db.session.commit()

    # 发送邮件通知
    if action == 'approve':
        approved_students = Student.query.filter(Student.id.in_(ids)).all()
        for s in approved_students:
            _notify_batch_review_result(s, 'approved', '')
    elif action == 'reject':
        rejected_students = Student.query.filter(Student.id.in_(ids)).all()
        for s in rejected_students:
            _notify_batch_review_result(s, 'rejected', '您的报名未通过审核，请联系管理员了解详情。')

    flash(f'已批量{new_status}', 'success')
    return redirect(url_for('admin.admin_dashboard', batch=batch))


def _notify_batch_review_result(student, status, reason=''):
    """发送批次审核结果通知邮件给学生（通过/拒绝）"""
    batch = student.batch
    dashboard_url = url_for('student.student_dashboard', _external=True)
    if status == 'approved':
        body = (
            f"同学 {student.name}，您好！\n\n"
            f"恭喜！您报名的以下批次已审核通过：\n\n"
            f"　　批次：{batch.display_title}\n"
            f"　　工种：{batch.skill.name if batch.skill else ''}\n"
            f"　　等级：{batch.skill_level}\n"
            f"　　批次号：{batch.batch_name}\n\n"
            f"请点击以下链接查看详情：\n"
            f"{dashboard_url}"
        )
        notify_student(student.user,
            f'技能认定资料收集系统 - 报名审核通过通知',
            body)
    else:
        body = (
            f"同学 {student.name}，您好！\n\n"
            f"很遗憾，您报名的以下批次审核未通过：\n\n"
            f"　　批次：{batch.display_title}\n"
            f"　　工种：{batch.skill.name if batch.skill else ''}\n"
            f"　　等级：{batch.skill_level}\n\n"
            + (f"原因：{reason}\n\n" if reason else '') +
            f"请登录系统查看详情或重新报名：\n"
            f"{dashboard_url}"
        )
        notify_student(student.user,
            f'技能认定资料收集系统 - 报名审核结果通知',
            body)


@admin_bp.route('/admin/approve_single/<int:student_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def approve_single(student_id):
    student = Student.query.get_or_404(student_id)
    action = request.form.get('action')
    batch = request.args.get('batch', '')
    if action not in ('approve', 'reject'):
        flash('无效操作', 'danger')
        return redirect(url_for('admin.admin_dashboard', batch=batch))
    if action == 'reject':
        reason = request.form.get('reason', '').strip()
        if len(reason) < 5 or len(reason) > 20:
            flash('拒绝原因需5-20字', 'danger')
            return redirect(url_for('admin.admin_dashboard', batch=batch))
        student.reject_reason = reason
        student.status = 'rejected'
    else:
        student.status = 'approved'
        student.reject_reason = ''
    db.session.commit()

    # 发送邮件通知
    if action == 'approve':
        _notify_batch_review_result(student, 'approved', '')
    elif action == 'reject':
        _notify_batch_review_result(student, 'rejected', student.reject_reason)

    flash(f'学生 {student.name} 已{student.status}', 'success')
    return redirect(url_for('admin.admin_dashboard', batch=batch))

@admin_bp.route('/admin/review/<int:student_id>')
@login_required
@role_required('admin', 'super_admin')
def review_student(student_id):
    student = Student.query.options(
    	joinedload(Student.batch).joinedload(ExamBatch.skill)
    ).get_or_404(student_id)
    profile = student.user.profile
    photo_url = edu_url = front_url = back_url = other_file_url = ''
    if profile:
        if profile.photo_path:
            photo_url = url_for('uploaded_file', filename=os.path.basename(profile.photo_path))
        if profile.edu_cert_path:
            edu_url = url_for('uploaded_file', filename=os.path.basename(profile.edu_cert_path))
        if profile.id_card_front_path:
            front_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_front_path))
        if profile.id_card_back_path:
            back_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_back_path))
        if profile.other_file_path:
            other_file_url = url_for('uploaded_file', filename=os.path.basename(profile.other_file_path))
    batch = request.args.get('batch', '')
    return render_template('student_review.html',
                           student=student,
                           photo_url=photo_url,
                           edu_url=edu_url,
                           id_card_front_url=front_url,
                           id_card_back_url=back_url,
                           other_file_url=other_file_url,
                           current_batch=batch)

# ---- 档案审核（管理员跨班） ----
@admin_bp.route('/admin/profiles')
@login_required
@role_required('admin', 'super_admin')
def admin_profiles():
    status = request.args.get('status', 'pending')
    name = request.args.get('name', '').strip()
    id_number = request.args.get('id_number', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    query = UserProfile.query.join(User).filter(User.role == 'student')

    # 添加系部过滤：如果当前是普通管理员，只显示其负责系部下的档案
    dept_ids = get_admin_department_ids()
    if dept_ids is not None:
        # 关联班级表，过滤班级所属系部在 dept_ids 中
        query = query.join(UserProfile.class_group).filter(ClassGroup.department_id.in_(dept_ids))

    if status != 'all':
        query = query.filter(UserProfile.status == status)
    if name:
        query = query.filter(UserProfile.name.contains(name))
    if id_number:
        query = query.filter(UserProfile.id_number == id_number)

    # 分页
    total = query.count()
    total_pages = (total + per_page - 1) // per_page
    if page < 1: page = 1
    if total_pages > 0 and page > total_pages: page = total_pages

    # 各状态计数
    base_q = UserProfile.query.join(User).filter(User.role == 'student')
    if dept_ids is not None:
        base_q = base_q.join(UserProfile.class_group).filter(ClassGroup.department_id.in_(dept_ids))
    status_counts = dict(
        db.session.query(UserProfile.status, db.func.count(UserProfile.id))
        .select_from(base_q.subquery()).group_by(UserProfile.status).all()
    )

    profiles = query.order_by(UserProfile.id.desc()).limit(per_page).offset((page - 1) * per_page).all()

    return render_template('admin_profiles.html',
                           profiles=profiles,
                           current_status=status,
                           name=name,
                           id_number=id_number,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           status_counts=status_counts)

#--------------------------------
@admin_bp.route('/admin/profile/review/<int:profile_id>')
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def review_profile(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    if current_user.role == 'headteacher':
        allowed_ids = [c.id for c in current_user.head_teacher.classes]
        if profile.class_id not in allowed_ids:
            flash('无权限查看非本班学生档案', 'danger')
            return redirect(url_for('teacher.teacher_profiles'))

    photo_url = edu_url = front_url = back_url = other_file_url = ''
    if profile.photo_path:
        photo_url = url_for('uploaded_file', filename=os.path.basename(profile.photo_path))
    if profile.edu_cert_path:
        edu_url = url_for('uploaded_file', filename=os.path.basename(profile.edu_cert_path))
    if profile.id_card_front_path:
        front_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_front_path))
    if profile.id_card_back_path:
        back_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_back_path))
    if profile.other_file_path:
        other_file_url = url_for('uploaded_file', filename=os.path.basename(profile.other_file_path))

    return render_template('admin_profile_review.html',
                           profile=profile,
                           photo_url=photo_url,
                           edu_url=edu_url,
                           id_card_front_url=front_url,
                           id_card_back_url=back_url,
                           other_file_url=other_file_url)

#-------------------
@admin_bp.route('/admin/profile/approve/<int:profile_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def approve_profile(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    # 班主任权限检查：必须是自己班的学生
    if current_user.role == 'headteacher':
        allowed_ids = [c.id for c in current_user.head_teacher.classes]
        if profile.class_id not in allowed_ids:
            flash('无权限审核非本班学生', 'danger')
            if current_user.role == 'headteacher':
                return redirect(url_for('teacher.teacher_profiles'))
            return redirect(url_for('admin.admin_profiles'))

    action = request.form.get('action')
    if action not in ('approve', 'reject'):
        flash('无效操作', 'danger')
        return redirect(url_for('admin.admin_profiles') if current_user.role != 'headteacher' else url_for('teacher.teacher_profiles'))
    if action == 'reject':
        reason = request.form.get('reason', '').strip()
        if len(reason) < 5 or len(reason) > 20:
            flash('拒绝原因需5-20字', 'danger')
            return redirect(url_for('admin.review_profile', profile_id=profile_id))
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
            f"您的个人档案已审核通过，现在可以报名参加技能认定了。\n\n"
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
    if current_user.role == 'headteacher':
        return redirect(url_for('teacher.teacher_profiles'))
    return redirect(url_for('admin.admin_profiles'))

#----------------------------
@admin_bp.route('/admin/reset_student_profile/<int:profile_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def reset_student_profile(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    if profile.status == 'pending':
        flash('档案当前即为待审核状态', 'warning')
        return redirect(request.referrer or url_for('admin.admin_profiles'))

    # 撤销有效报名并发送通知（邮件可能失败，但不影响流程）
    revoked = revoke_student_registrations_and_notify(profile)

    profile.status = 'pending'
    profile.reject_reason = ''
    # 写入重置提示
    if revoked:
        profile.user.reset_note = f"您的档案已被管理员重置为待审核，以下批次报名已被撤销：{', '.join(revoked)}。请重新编辑档案并提交。"
    else:
        profile.user.reset_note = "您的档案已被管理员重置为待审核，请重新编辑档案并提交。"
    db.session.commit()

    flash(f'已重置学生 {profile.name} 的档案状态为待审核，通知已发送。', 'success')
    return redirect(request.referrer or url_for('admin.admin_profiles'))

# ---- 业务管理员个人信息 ----
@admin_bp.route('/admin/profile', methods=['GET', 'POST'])
@login_required
@role_required('admin')
def admin_profile():
    ap = current_user.admin_profile
    if not ap:
        ap = AdminProfile(user_id=current_user.id, name=current_user.username, id_number='000000000000000000')
        db.session.add(ap)
        db.session.commit()
        flash('检测到您的管理员资料不完整，请尽快完善个人信息', 'warning')

    if request.method == 'POST':
        new_password = request.form.get('password')
        if new_password:
            valid, msg = validate_password_strength(new_password)
            if not valid:
                flash(msg, 'danger')
            else:
                current_user.password_hash = generate_password_hash(new_password)
                flash('密码修改成功', 'success')

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

        phone = request.form.get('phone')
        if phone:
            ap.phone = phone

        db.session.commit()
        return redirect(url_for('admin.admin_profile'))
    return render_template('admin_profile.html', ap=ap)

@admin_bp.route('/admin/update_phone', methods=['POST'])
@login_required
@role_required('admin')
def admin_update_phone():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return jsonify({'status': 'error', 'msg': '手机号不能为空'})
    if not validate_phone(phone):
        return jsonify({'status': 'error', 'msg': '手机号格式不正确'})

    # 全局唯一性检查：排除自己
    existing_admin = AdminProfile.query.filter(
        AdminProfile.phone == phone,
        AdminProfile.user_id != current_user.id
    ).first()
    existing_teacher = HeadTeacher.query.filter(HeadTeacher.phone == phone).first()
    existing_student = UserProfile.query.filter(UserProfile.phone == phone).first()

    if existing_admin:
        return jsonify({'status': 'error', 'msg': '该手机号已被其他业务管理员使用'})
    if existing_teacher:
        return jsonify({'status': 'error', 'msg': '该手机号已被某位班主任使用'})
    if existing_student:
        return jsonify({'status': 'error', 'msg': '该手机号已被某位学生使用'})

    current_user.admin_profile.phone = phone
    db.session.commit()
    return jsonify({'status': 'success'})

@admin_bp.route('/admin/reset_email', methods=['POST'])
@login_required
@role_required('admin')
def admin_reset_email():
    current_user.email_verified = False
    db.session.commit()
    return jsonify({'status': 'success'})

# ---- 管理员对班主任的操作 ----
@admin_bp.route('/admin/teachers')
@login_required
@role_required('admin', 'super_admin')
def manage_teachers():
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限访问班主任管理，仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))

    search = request.args.get('search', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 50

    is_search = len(search) >= 2

    query = HeadTeacher.query.join(User).options(joinedload(HeadTeacher.user))
     
    dept_ids = get_admin_department_ids()
    if dept_ids is not None:
        query = query.join(HeadTeacher.classes).filter(ClassGroup.department_id.in_(dept_ids))

    if is_search:
        query = query.filter(
            (HeadTeacher.name.contains(search)) | (User.username.contains(search))
        )

    if not is_search:
        # 默认最新50条
        teachers = query.order_by(HeadTeacher.id.desc()).limit(per_page).all()
        total = None
        total_pages = None
    else:
        # 搜索时启用分页
        total = query.count()
        total_pages = (total + per_page - 1) // per_page
        if page < 1: page = 1
        if total_pages > 0 and page > total_pages: page = total_pages
        teachers = query.order_by(HeadTeacher.id.desc()).limit(per_page).offset((page - 1) * per_page).all()

    return render_template('manage_teachers.html',
                           teachers=teachers,
                           search=search,
                           page=page,
                           total_pages=total_pages,
                           total=total,
                           is_search=is_search)

#--------------------------
@admin_bp.route('/admin/teachers/create', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def create_teacher():
    if current_user.role == 'admin' and not current_user.admin_profile.is_global:
        flash('您没有权限访问班主任管理，仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))

# 在 create_teacher 函数开头同理添加

    # 获取可用系部：普通管理员只能看到自己管辖的系部，全局管理员看到所有
    if current_user.role == 'admin':
        ap = current_user.admin_profile
        if ap and not ap.is_global:
            departments = ap.departments   # 普通管理员
        else:
            departments = Department.query.filter_by(is_deleted=False).all()
    else:
        departments = Department.query.filter_by(is_deleted=False).all()

    if request.method == 'POST':
        # 检查系部是否存在
        if Department.query.count() == 0:
            flash('请先创建系部，再创建班主任', 'danger')
            return render_template('create_teacher.html', departments=departments)

        name = request.form.get('name', '').strip()
        id_number = request.form.get('id_number', '').strip()
        gender = request.form.get('gender', '').strip()
        department_ids = request.form.getlist('department_ids')

        if not name or not id_number or not gender:
            flash('所有字段均为必填', 'danger')
            return render_template('create_teacher.html', departments=departments)

        if not re.match(r'^\d{17}[\dXx]$', id_number):
            flash('身份证号格式不正确', 'danger')
            return render_template('create_teacher.html', departments=departments)

        if HeadTeacher.query.filter_by(id_number=id_number).first():
            flash('该身份证号已存在', 'danger')
            return render_template('create_teacher.html', departments=departments)

        # 生成账号
        username = 'ht' + ''.join(random.choices(string.digits, k=7))
        while User.query.filter_by(username=username).first():
            username = 'ht' + ''.join(random.choices(string.digits, k=7))

        new_pwd = generate_random_password()
        user = User(
            username=username,
            password_hash=generate_password_hash(new_pwd),
            role='headteacher'
        )
        db.session.add(user)
        db.session.flush()

        ht = HeadTeacher(
            user_id=user.id,
            name=name,
            id_number=id_number,
            gender=gender
        )

        # 关联系部（仅允许在可用系部范围内选择）
        if department_ids:
            valid_ids = [d.id for d in departments]
            selected_ids = [int(did) for did in department_ids if int(did) in valid_ids]
            if selected_ids:
                ht.departments = Department.query.filter(Department.id.in_(selected_ids)).all()

        db.session.add(ht)
        db.session.commit()

        audit_log('CREATE_TEACHER', f'{name}({username})')
        # 将凭证邮件发送给操作者留底
        email_ok, email_msg = send_credentials_notification(current_user, name, username, 'headteacher', new_pwd, '创建')
        flash(f'班主任 {name} 创建成功，账号：{username}，初始密码：{new_pwd}，账户凭证留底已发往 {mask_email(current_user.email)} 邮箱', 'success')
        if not email_ok:
            flash(email_msg, 'warning')
        return redirect(url_for('admin.manage_teachers'))

    return render_template('create_teacher.html', departments=departments)

@admin_bp.route('/admin/teacher/<int:teacher_id>')
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def view_teacher(teacher_id):
    ht = HeadTeacher.query.get_or_404(teacher_id)
    return render_template('view_teacher.html', teacher=ht)

#------------------------
@admin_bp.route('/admin/reset_teacher_email/<int:teacher_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def reset_teacher_email(teacher_id):
    ht = HeadTeacher.query.get_or_404(teacher_id)
    if not ht.user.email or '@' not in ht.user.email:
        flash(f'班主任 {ht.name} 尚未填写邮箱或格式不正确，无法重置邮箱验证状态', 'danger')
        return redirect(request.referrer or url_for('admin.manage_teachers'))
    ht.user.email_verified = False
    db.session.commit()
    flash(f'已重置班主任 {ht.name} 的邮箱验证状态', 'success')
    return redirect(request.referrer or url_for('admin.manage_teachers'))
#---------更改班主任锁定解锁状态----------------
@admin_bp.route('/admin/teachers/toggle/<int:teacher_id>')
@login_required
@role_required('admin', 'super_admin')
def toggle_teacher_activation(teacher_id):
    ht = HeadTeacher.query.get_or_404(teacher_id)
    
    # 锁定前检查是否有班级
    if ht.user.is_active and ht.classes:
        flash(f'班主任 {ht.name} 名下还有 {len(ht.classes)} 个班级未转移，请先转移后再锁定', 'danger')
        return redirect(url_for('admin.manage_teachers'))
    
    ht.user.is_active = not ht.user.is_active
    status = '解锁' if ht.user.is_active else '锁定'
    db.session.commit()
    audit_log('TOGGLE_TEACHER', f'{ht.name}({ht.user.username}) -> {status}')
    flash(f'班主任 {ht.name} 已{status}', 'success')
    return redirect(url_for('admin.manage_teachers'))
    
#----------调整班级-----------------
@admin_bp.route('/admin/teachers/adjust_classes/<int:ht_id>', methods=['GET', 'POST'])
@login_required
@role_required('admin', 'super_admin')
def adjust_teacher_classes(ht_id):
    current_ht = HeadTeacher.query.get_or_404(ht_id)
    
    if not current_ht.user.is_active:
        flash('班主任已锁定，无法调整班级', 'danger')
        return redirect(url_for('admin.manage_teachers'))
    
    admin_dept_ids = get_admin_department_ids()
    
    if request.method == 'POST':
        class_ids = request.form.getlist('class_ids')
        target_ht_id = request.form.get('target_ht_id', '').strip()
        lock_source = request.form.get('lock_source') == '1'
        
        if not class_ids:
            flash('请选择至少一个要转移的班级', 'danger')
            return redirect(url_for('admin.adjust_teacher_classes', ht_id=ht_id))
        
        if not target_ht_id:
            flash('请选择目标班主任', 'danger')
            return redirect(url_for('admin.adjust_teacher_classes', ht_id=ht_id))
        
        target_ht = HeadTeacher.query.get(int(target_ht_id))
        if not target_ht:
            flash('目标班主任不存在', 'danger')
            return redirect(url_for('admin.adjust_teacher_classes', ht_id=ht_id))
        
        if target_ht.id == current_ht.id:
            flash('不能选择自己作为目标班主任', 'danger')
            return redirect(url_for('admin.adjust_teacher_classes', ht_id=ht_id))
        
        if admin_dept_ids is not None:
            for cid in class_ids:
                cls = ClassGroup.query.get(int(cid))
                if cls and cls.department_id not in admin_dept_ids:
                    flash(f'您没有权限操作系部 {cls.department.name} 的班级', 'danger')
                    return redirect(url_for('admin.adjust_teacher_classes', ht_id=ht_id))
        
        if not current_ht.user.email_verified or not target_ht.user.email_verified:
            flash('双方班主任的邮箱必须都已验证，才能调整班级', 'danger')
            return redirect(url_for('admin.adjust_teacher_classes', ht_id=ht_id))
        
        for cid in class_ids:
            cls = ClassGroup.query.get(int(cid))
            if cls and cls in current_ht.classes:
                current_ht.classes.remove(cls)
                target_ht.classes.append(cls)
        
        for ht in [current_ht, target_ht]:
            dept_ids = set()
            for c in ht.classes:
                if c.department:
                    dept_ids.add(c.department)
            ht.departments = list(dept_ids)
        
        if lock_source:
            current_ht.user.is_active = False
        
        db.session.commit()
        flash(f'已将 {len(class_ids)} 个班级转移给 {target_ht.name}', 'success')
        return redirect(url_for('admin.manage_teachers'))
    
    query = HeadTeacher.query.join(User).filter(
        HeadTeacher.id != ht_id,
        User.is_active == True
    )
    
    if admin_dept_ids is not None:
        query = query.filter(HeadTeacher.departments.any(Department.id.in_(admin_dept_ids)))
    
    other_headteachers = query.all()
    
    if admin_dept_ids is not None:
        departments = Department.query.filter(Department.id.in_(admin_dept_ids), Department.is_deleted == False).order_by(Department.name).all()
    else:
        departments = Department.query.filter_by(is_deleted=False).order_by(Department.name).all()
    
    return render_template('adjust_teacher_classes.html',
                           current_ht=current_ht,
                           other_headteachers=other_headteachers,
                           departments=departments)

# ---- 管理员修改学生邮箱 ----
@admin_bp.route('/admin/update_student_email/<int:profile_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin', 'headteacher')
def update_student_email(profile_id):
    profile = UserProfile.query.get_or_404(profile_id)
    if current_user.role == 'headteacher':
        allowed_ids = [c.id for c in current_user.head_teacher.classes]
        if profile.class_id not in allowed_ids:
            flash('无权限修改非本班学生信息', 'danger')
            return redirect(url_for('teacher.teacher_profiles'))

    new_email = request.form.get('new_email', '').strip()
    if not new_email or '@' not in new_email:
        flash('邮箱格式不正确', 'danger')
        return redirect(request.referrer or url_for('admin.admin_profiles'))

    if User.query.filter(User.email == new_email, User.id != profile.user_id).first():
        flash('该邮箱已被其他用户使用', 'danger')
        return redirect(request.referrer or url_for('admin.admin_profiles'))

    old_email = profile.user.email
    user = profile.user
    user.email = new_email
    user.email_verified = False
    user.reset_note = f"您的邮箱已被管理员修改为 {new_email}，请登录后重新验证邮箱。如已验证，请忽略！"
    db.session.commit()

    try:
        msg = Message('技能认定资料收集系统 - 邮箱变更通知', recipients=[new_email])
        msg.charset = 'utf-8'
        msg.body = (
            f"您的技能认定资料收集系统登录邮箱已由管理员 {current_user.username} 从 {old_email} 更换为 {new_email}。\n"
            f"从即刻起，您将使用此邮箱接收登录验证码。\n\n"
            f"⚠️ 如果这不是您本人主动要求更换，请立即联系您的班主任或管理员！\n\n"
            f"系统操作时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}"
        )
        current_app.extensions['mail'].send(msg)
        flash(f'已将 {profile.name} 的邮箱更换为 {new_email}，并已通知学生。', 'success')
    except Exception as e:
        current_app.logger.error(f'通知邮件发送失败：{e}')
        flash(f'邮箱已更换为 {new_email}，但通知邮件发送失败，请手动告知学生。', 'warning')

    return redirect(request.referrer or url_for('admin.admin_profiles'))

# ---- 管理员重置验证次数 ----
@admin_bp.route('/admin/reset_verification_attempts/<int:user_id>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def reset_verification_attempts(user_id):
    user = User.query.get_or_404(user_id)
    user.verification_attempts = 0
    db.session.commit()
    flash(f'已重置 {user.username} 的验证码错误计数', 'success')
    return redirect(request.referrer or url_for('admin.admin_dashboard'))

# ---- 导出功能 ----
@admin_bp.route('/admin/export_page')
@login_required
@role_required('admin', 'super_admin')
def export_page():
    """保留兼容，直接跳转到批次管理"""
    return redirect(url_for('admin.manage_batches'))

@admin_bp.route('/admin/export/<batch>', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def export_batch(batch):
    batch_obj = ExamBatch.query.filter_by(batch_name=batch).first()
    if not batch_obj:
        flash(f'批次 {batch} 不存在，将前往批次管理页面查看可用批次。', 'danger')
        return redirect(url_for('admin.manage_batches'))
    if batch_obj.is_locked:
        flash(f'批次 {batch} 已被锁定，无法导出', 'warning')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_obj.batch_name, status='approved'))

    # 支持选择性导出：前端传入 student_ids，否则导出全部已通过
    selected_ids = request.form.getlist('student_ids')
    if selected_ids:
        students = Student.query.filter(
            Student.id.in_(selected_ids),
            Student.batch_id == batch_obj.id,
            Student.status == 'approved'
        ).all()
    else:
        students = Student.query.filter_by(batch_id=batch_obj.id, status='approved').all()

    if not students:
        flash(f'没有可导出的已通过学生', 'warning')
        return redirect(url_for('admin.batch_reviews', batch_name=batch_obj.batch_name, status='approved'))

    tmpdir = tempfile.mkdtemp()
    try:
        for s in students:
            stu_dir = os.path.join(tmpdir, s.id_number)
            os.makedirs(stu_dir, exist_ok=True)
            profile = s.user.profile
            if profile:
                if profile.photo_path:
                    src = os.path.join(current_app.config['UPLOAD_FOLDER'], profile.photo_path)
                    if os.path.exists(src):
                        ext = os.path.splitext(profile.photo_path)[1]
                        shutil.copy2(src, os.path.join(stu_dir, f"{s.id_number}{ext}"))
                if profile.edu_cert_path:
                    src = os.path.join(current_app.config['UPLOAD_FOLDER'], profile.edu_cert_path)
                    if os.path.exists(src):
                        ext = os.path.splitext(profile.edu_cert_path)[1]
                        shutil.copy2(src, os.path.join(stu_dir, f"{s.id_number}-edu{ext}"))
                if profile.id_card_front_path:
                    src = os.path.join(current_app.config['UPLOAD_FOLDER'], profile.id_card_front_path)
                    if os.path.exists(src):
                        ext = os.path.splitext(profile.id_card_front_path)[1]
                        shutil.copy2(src, os.path.join(stu_dir, f"身份证正面{ext}"))
                if profile.id_card_back_path:
                    src = os.path.join(current_app.config['UPLOAD_FOLDER'], profile.id_card_back_path)
                    if os.path.exists(src):
                        ext = os.path.splitext(profile.id_card_back_path)[1]
                        shutil.copy2(src, os.path.join(stu_dir, f"身份证反面{ext}"))
        export_dir = current_app.config.get('EXPORT_FOLDER', os.path.join(os.path.dirname(current_app.config['UPLOAD_FOLDER']), 'exports'))
        os.makedirs(export_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d%H%M')
        zip_filename = f"{batch_obj.batch_name}_{batch_obj.skill.name}_{batch_obj.skill_level}_{ts}.zip"
        zip_base = os.path.join(export_dir, zip_filename.rsplit('.', 1)[0])
        shutil.make_archive(zip_base, 'zip', tmpdir)
        zip_path = f"{zip_base}.zip"
        return send_file(zip_path, as_attachment=True, download_name=zip_filename)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



# ===================== 通知管理（全局管理员入口） =====================
@admin_bp.route('/admin/notices')
@login_required
@role_required('admin', 'super_admin')
def manage_notices():
    if current_user.role == 'admin' and not (current_user.admin_profile and current_user.admin_profile.is_global):
        flash('仅限全局管理员', 'danger')
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
        base_url='admin.manage_notices',
        save_endpoint='admin.save_notice',
        toggle_endpoint='admin.toggle_notice',
        delete_endpoint='admin.delete_notice',
        notices=notices, page=page, total_pages=total_pages, total=total,
        current_status=status, search=search)


# ===================== 通知操作（全局管理员入口） =====================
@admin_bp.route('/admin/notice/save', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def save_notice():
    if current_user.role == 'admin' and not (current_user.admin_profile and current_user.admin_profile.is_global):
        flash('仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))
    notice_id = request.form.get('id', type=int)
    title = request.form.get('title', '').strip()
    content = request.form.get('content', '').strip()
    importance = request.form.get('importance', 'normal')
    is_public = request.form.get('is_public') == '1'
    is_published = request.form.get('is_published') == '1'
    redirect_status = request.form.get('redirect_status', 'published')

    if not title:
        flash('标题不能为空', 'danger')
        return redirect(url_for('admin.manage_notices', status=redirect_status))

    if notice_id:
        n = Notice.query.get(notice_id)
        if n:
            n.title = title; n.content = content
            n.importance = importance; n.is_public = is_public
            n.is_published = is_published
    else:
        db.session.add(Notice(title=title, content=content, importance=importance,
                              is_public=is_public, is_published=is_published))
    db.session.commit()
    flash('通知已保存', 'success')
    return redirect(url_for('admin.manage_notices', status=redirect_status))


@admin_bp.route('/admin/notice/<int:notice_id>/toggle', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def toggle_notice(notice_id):
    if current_user.role == 'admin' and not (current_user.admin_profile and current_user.admin_profile.is_global):
        flash('仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))
    n = Notice.query.get_or_404(notice_id)
    n.is_published = not n.is_published
    db.session.commit()
    redirect_status = request.form.get('redirect_status', 'published')
    flash('已发布' if n.is_published else '已撤回', 'success')
    return redirect(url_for('admin.manage_notices', status=redirect_status))


@admin_bp.route('/admin/notice/<int:notice_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'super_admin')
def delete_notice(notice_id):
    if current_user.role == 'admin' and not (current_user.admin_profile and current_user.admin_profile.is_global):
        flash('仅限全局管理员', 'danger')
        return redirect(url_for('auth.dashboard'))
    n = Notice.query.get_or_404(notice_id)
    n.is_deleted = True
    db.session.commit()
    redirect_status = request.form.get('redirect_status', 'published')
    flash('通知已删除', 'success')
    return redirect(url_for('admin.manage_notices', status=redirect_status))
