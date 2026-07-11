"""学生路由 Blueprint"""
import os
import re
import time
import random
from datetime import datetime
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash,
    session, jsonify, current_app
)
from flask_login import login_required, current_user
from flask_mail import Message
from werkzeug.security import generate_password_hash

from models import db, User, UserProfile, ClassGroup, ExamBatch, Student, Department, batch_classes
from sqlalchemy.orm import joinedload
from utils import validate_id_number_checksum, role_required
from services import validate_phone, validate_password_strength
from shared import (
    validate_image, validate_other_file, save_file, send_verify_code,
    notify_student
)

student_bp = Blueprint('student', __name__)


@student_bp.route('/student/account')
@login_required
@role_required('student')
def student_account():
    return render_template('student_account.html')


@student_bp.route('/student/profile', methods=['GET', 'POST'])
@login_required
@role_required('student')
def edit_profile():
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()

    if request.method == 'GET':
        departments = Department.query.order_by(Department.name).all()
        all_classes = ClassGroup.query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False).all()
        return render_template('edit_profile.html', profile=profile, departments=departments, all_classes=all_classes)

    if request.method == 'POST':
        if profile and profile.status == 'approved':
            flash('您的档案已审核通过，无法修改。如需更改手机号，请使用页面上的保存按钮单独更新。', 'danger')
            return redirect(url_for('student.edit_profile'))

        name = request.form.get('name', '').strip()
        id_number = request.form.get('id_number', '').strip()
        phone = request.form.get('phone', '').strip()
        gender = request.form.get('gender', '').strip()
        photo = request.files.get('photo')
        edu_cert = request.files.get('edu_cert')
        id_card_front = request.files.get('id_card_front')
        id_card_back = request.files.get('id_card_back')
        other_file = request.files.get('other_file')

        class_id = None
        if not profile:
            class_id = request.form.get('class_id')

        errors = []
        if not name: errors.append('姓名不能为空')
        if not id_number or not validate_id_number_checksum(id_number):
            errors.append('身份证号格式或校验码不正确')
        if not phone:
            errors.append('手机号不能为空')
        elif not validate_phone(phone):
            errors.append('手机号格式不正确，应为11位手机号码')
        else:
            existing_phone = UserProfile.query.filter(
                UserProfile.phone == phone,
                UserProfile.user_id != current_user.id
            ).first()
            if existing_phone:
                errors.append('该手机号已被其他学生使用')
        if not profile and not class_id:
            errors.append('请选择班级')

        existing = UserProfile.query.filter(UserProfile.id_number == id_number,
                                            UserProfile.user_id != current_user.id).first()
        if existing: errors.append('该身份证号已被使用')

        if not profile:
            if not photo or not photo.filename:
                errors.append('请上传证件照')
            if not id_card_front or not id_card_front.filename:
                errors.append('请上传身份证人像面')
            if not id_card_back or not id_card_back.filename:
                errors.append('请上传身份证国徽面')

        photo_error = validate_image(photo, '证件照', max_size_mb=0.2, exact_size=(295, 413))
        if photo_error: errors.append(photo_error)
        edu_error = validate_image(edu_cert, '学历证明', max_size_mb=2)
        if edu_error: errors.append(edu_error)
        front_error = validate_image(id_card_front, '身份证人像面', max_size_mb=2)
        if front_error: errors.append(front_error)
        back_error = validate_image(id_card_back, '身份证国徽面', max_size_mb=2)
        if back_error: errors.append(back_error)
        other_error = validate_other_file(other_file)
        if other_error: errors.append(other_error)

        if errors:
            for e in errors: flash(e, 'danger')
            departments = Department.query.order_by(Department.name).all()
            all_classes = ClassGroup.query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False).all()
            return render_template('edit_profile.html', profile=profile, departments=departments, all_classes=all_classes)

        department_name = profile.department_name if profile else ''
        if class_id:
            class_group = ClassGroup.query.get(int(class_id))
            if class_group and class_group.department:
                department_name = class_group.department.name

        try:
            photo_fname = profile.photo_path if profile else None
            if photo and photo.filename:
                photo_fname = save_file(photo, id_number, '')

            edu_fname = profile.edu_cert_path if profile else None
            if edu_cert and edu_cert.filename:
                edu_fname = save_file(edu_cert, id_number, 'edu')

            front_fname = profile.id_card_front_path if profile else None
            if id_card_front and id_card_front.filename:
                front_fname = save_file(id_card_front, id_number, 'idfront')

            back_fname = profile.id_card_back_path if profile else None
            if id_card_back and id_card_back.filename:
                back_fname = save_file(id_card_back, id_number, 'idback')

            other_fname = profile.other_file_path if profile else None
            if other_file and other_file.filename:
                ext = other_file.filename.rsplit('.', 1)[1].lower()
                other_fname = f"{id_number}_other.{ext}"
                other_file.save(os.path.join(current_app.config['UPLOAD_FOLDER'], other_fname))
        except Exception as e:
            flash(f'文件保存失败: {str(e)}', 'danger')
            departments = Department.query.order_by(Department.name).all()
            all_classes = ClassGroup.query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False).all()
            return render_template('edit_profile.html', profile=profile, departments=departments, all_classes=all_classes)

        if profile:
            profile.name = name
            profile.phone = phone
            profile.gender = gender
            if not profile.id_number:
                profile.id_number = id_number
            profile.photo_path = photo_fname
            profile.edu_cert_path = edu_fname
            profile.id_card_front_path = front_fname
            profile.id_card_back_path = back_fname
            profile.other_file_path = other_fname
            profile.department_name = department_name or profile.department_name
            if profile.status == 'rejected':
                profile.status = 'pending'
                profile.reject_reason = ''
        else:
            profile = UserProfile(
                user_id=current_user.id,
                name=name,
                id_number=id_number,
                phone=phone,
                gender=gender,
                photo_path=photo_fname,
                edu_cert_path=edu_fname,
                id_card_front_path=front_fname,
                id_card_back_path=back_fname,
                other_file_path=other_fname,
                class_id=int(class_id) if class_id else None,
                department_name=department_name,
                status='pending'
            )
            db.session.add(profile)

        current_user.reset_note = ''
        db.session.commit()
        flash('个人信息已保存，等待审核', 'success')
        return redirect(url_for('student.student_profile_view'))


@student_bp.route('/student/update_phone', methods=['POST'])
@login_required
@role_required('student')
def student_update_phone():
    phone = request.form.get('phone', '').strip()
    if not phone:
        return jsonify({'status': 'error', 'msg': '手机号不能为空'})
    if not validate_phone(phone):
        return jsonify({'status': 'error', 'msg': '手机号格式不正确'})

    existing = UserProfile.query.filter(
        UserProfile.phone == phone,
        UserProfile.user_id != current_user.id
    ).first()
    if existing:
        return jsonify({'status': 'error', 'msg': '该手机号已被其他学生使用'})

    profile = current_user.profile
    if not profile:
        return jsonify({'status': 'error', 'msg': '档案尚未创建'})

    profile.phone = phone
    db.session.commit()
    return jsonify({'status': 'success', 'msg': '手机号已保存'})


@student_bp.route('/student/request_change_email', methods=['POST'])
@login_required
@role_required('student')
def request_change_email():
    new_email = request.form.get('email', '').strip()
    if not new_email or '@' not in new_email:
        return jsonify({'status': 'error', 'msg': '邮箱格式不正确'})
    if User.query.filter(User.email == new_email, User.id != current_user.id).first():
        return jsonify({'status': 'error', 'msg': '该邮箱已被其他用户使用'})
    code = ''.join(random.choices('0123456789', k=6))
    session['change_email_code'] = code
    session['change_email_code_time'] = time.time()
    session['change_email_new_email'] = new_email
    mail = current_app.extensions['mail']
    msg = Message('技能认定资料收集系统 - 邮箱验证码', recipients=[new_email])
    msg.charset = 'utf-8'
    msg.body = f'您的邮箱验证码是：{code}，有效期5分钟。'
    try:
        mail.send(msg)
        return jsonify({'status': 'success', 'msg': '验证码已发送'})
    except Exception as e:
        current_app.logger.error(f'邮件发送失败：{e}')
        return jsonify({'status': 'error', 'msg': '邮件发送失败，请稍后再试'})


@student_bp.route('/student/verify_change_email', methods=['POST'])
@login_required
@role_required('student')
def verify_change_email():
    code = request.form.get('code', '').strip()
    saved_code = session.get('change_email_code')
    if not saved_code or code != saved_code:
        return jsonify({'status': 'error', 'msg': '验证码错误'})
    if time.time() - session.get('change_email_code_time', 0) > 300:
        return jsonify({'status': 'error', 'msg': '验证码已过期'})
    new_email = session.get('change_email_new_email')
    if not new_email:
        return jsonify({'status': 'error', 'msg': '请先获取验证码'})

    current_user.email = new_email
    current_user.email_verified = True
    db.session.commit()

    session.pop('change_email_code', None)
    session.pop('change_email_code_time', None)
    session.pop('change_email_new_email', None)

    return jsonify({'status': 'success', 'msg': '邮箱更换成功，新邮箱已自动验证'})


@student_bp.route('/student/profile_view')
@login_required
@role_required('student')
def student_profile_view():
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash('请先完善个人资料', 'warning')
        return redirect(url_for('student.edit_profile'))

    photo_url = edu_url = front_url = back_url = other_url = ''
    if profile.photo_path:
        photo_url = url_for('uploaded_file', filename=os.path.basename(profile.photo_path))
    if profile.edu_cert_path:
        edu_url = url_for('uploaded_file', filename=os.path.basename(profile.edu_cert_path))
    if profile.id_card_front_path:
        front_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_front_path))
    if profile.id_card_back_path:
        back_url = url_for('uploaded_file', filename=os.path.basename(profile.id_card_back_path))
    if profile.other_file_path:
        other_url = url_for('uploaded_file', filename=os.path.basename(profile.other_file_path))

    return render_template('student_profile_view.html',
                           profile=profile,
                           photo_url=photo_url,
                           edu_url=edu_url,
                           front_url=front_url,
                           back_url=back_url,
                           other_url=other_url)


@student_bp.route('/student/change_password', methods=['GET', 'POST'])
@login_required
@role_required('student')
def student_change_password():
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()

        if current_user.verification_attempts >= 3:
            flash('验证码错误次数过多，修改密码功能已锁定。请联系班主任或管理员解锁。', 'danger')
            return render_template('student_change_password.html')

        saved_code = session.get('email_verify_code')
        code_time = session.get('email_verify_code_time', 0)
        if not saved_code or not code:
            flash('请先获取验证码', 'danger')
            return render_template('student_change_password.html')
        if code != saved_code:
            current_user.verification_attempts += 1
            db.session.commit()
            attempts_left = 3 - current_user.verification_attempts
            if attempts_left > 0:
                flash(f'验证码错误，还剩 {attempts_left} 次尝试机会', 'danger')
            else:
                flash('验证码错误次数过多，修改密码功能已锁定。请联系班主任或管理员解锁。', 'danger')
            return render_template('student_change_password.html')
        if time.time() - code_time > 300:
            flash('验证码已过期，请重新获取', 'danger')
            return render_template('student_change_password.html')

        current_user.verification_attempts = 0

        if not new_password:
            flash('新密码不能为空', 'danger')
            return render_template('student_change_password.html')
        valid, msg = validate_password_strength(new_password)
        if not valid:
            flash(msg, 'danger')
            return render_template('student_change_password.html')
        if new_password != confirm_password:
            flash('两次输入的密码不一致', 'danger')
            return render_template('student_change_password.html')

        current_user.password_hash = generate_password_hash(new_password)
        db.session.commit()

        session.pop('email_verify_code', None)
        session.pop('email_verify_code_time', None)
        session.pop('send_code_time', None)

        flash('密码修改成功', 'success')
        return redirect(url_for('student.student_dashboard'))

    return render_template('student_change_password.html')


@student_bp.route('/student/withdraw/<int:student_id>', methods=['POST'])
@login_required
@role_required('student')
def withdraw_application(student_id):
    registration = Student.query.get_or_404(student_id)
    if registration.user_id != current_user.id:
        flash('无权操作该报名', 'danger')
        return redirect(url_for('student.student_dashboard'))
    if registration.status not in ('pending', 'rejected'):
        flash('当前状态不允许退出', 'danger')
        return redirect(url_for('student.student_dashboard'))
    registration.status = 'withdrawn'
    db.session.commit()

    # 发送邮件通知
    batch = registration.batch
    if batch:
        dashboard_url = url_for('student.student_dashboard', _external=True)
        body = (
            f"同学 {registration.name}，您好！\n\n"
            f"您已成功退出以下批次的报名：\n\n"
            f"　　批次：{batch.display_title}\n"
            f"　　工种：{batch.skill.name if batch.skill else ''}\n"
            f"　　等级：{batch.skill_level}\n\n"
            f"如需重新报名，请登录系统操作：\n"
            f"{dashboard_url}"
        )
        notify_student(registration.user, '技能认定资料收集系统 - 报名已退出', body)

    flash('已退出该批次报名', 'success')
    return redirect(url_for('student.student_dashboard'))


@student_bp.route('/student/dashboard')
@login_required
@role_required('student')
def student_dashboard():
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash('请先完善个人资料', 'warning')
        return redirect(url_for('student.edit_profile'))

    if profile.status != 'approved':
        flash('你的个人档案等待班主任审核，通过后可以查看报名的批次进行报名。未通过审核前不允许报名批次。', 'warning')
        return redirect(url_for('student.student_profile_view'))

    now = datetime.now()

    if profile.class_id:
        open_batches = ExamBatch.query.join(batch_classes).join(ClassGroup).filter(
            ExamBatch.start_time <= now,
            ExamBatch.end_time >= now,
            ExamBatch.is_archived == False,
            ExamBatch.is_locked == False,
            ClassGroup.id == profile.class_id
        ).options(joinedload(ExamBatch.skill)).all()
    else:
        open_batches = []

    my_regs = Student.query.options(
        joinedload(Student.batch).joinedload(ExamBatch.skill)
    ).filter_by(user_id=current_user.id).all()
    applied_batch_ids = {s.batch_id for s in my_regs if s.status != 'withdrawn'}
    reg_map = {s.batch_id: s for s in my_regs}

    approved_active = Student.query.filter(
        Student.user_id == current_user.id,
        Student.status == 'approved',
        Student.batch.has(ExamBatch.end_time >= now)
    ).first()
    can_transfer = (not approved_active and profile.transfer_status != 'pending')

    target_class_name = None
    target_class_no = None
    if profile.transfer_class_id:
        target_class = ClassGroup.query.get(profile.transfer_class_id)
        if target_class:
            target_class_name = target_class.name
            target_class_no = target_class.class_no

    available_batches = ExamBatch.query.filter(
        ExamBatch.is_archived == False
    ).options(joinedload(ExamBatch.skill)).order_by(ExamBatch.start_time.desc()).all()

    if profile.class_id:
        expired_batches = ExamBatch.query.join(batch_classes).join(ClassGroup).filter(
            (ExamBatch.end_time < now) | (ExamBatch.is_locked == True),
            ExamBatch.is_archived == False,
            ClassGroup.id == profile.class_id
        ).options(joinedload(ExamBatch.skill)).all()
    else:
        expired_batches = []

    departments = Department.query.order_by(Department.name).all()

    return render_template('student_dashboard.html',
                           profile=profile,
                           open_batches=open_batches,
                           expired_batches=expired_batches,
                           students=my_regs,
                           now=now,
                           applied_batch_ids=applied_batch_ids,
                           can_transfer=can_transfer,
                           target_class_name=target_class_name,
                           target_class_no=target_class_no,
                           reg_map=reg_map,
                           departments=departments)


@student_bp.route('/student/apply/<int:batch_id>', methods=['POST'])
@login_required
@role_required('student')
def apply_batch(batch_id):
    batch = ExamBatch.query.get_or_404(batch_id)
    now = datetime.now()
    if not (batch.start_time <= now <= batch.end_time):
        flash('该批次不在有效期内', 'danger')
        return redirect(url_for('student.student_dashboard'))
    if batch.is_locked:
        flash('该批次已被锁定，无法报名', 'danger')
        return redirect(url_for('student.student_dashboard'))

    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash('请先完善个人资料', 'danger')
        return redirect(url_for('student.edit_profile'))
    if profile.status != 'approved':
        flash('您的个人档案尚未通过审核，无法报名', 'danger')
        return redirect(url_for('student.student_dashboard'))

    existing = Student.query.filter_by(user_id=current_user.id, batch_id=batch_id).first()
    if existing:
        if existing.status == 'withdrawn':
            existing.status = 'pending'
            existing.reject_reason = ''
            db.session.commit()
            flash('已重新报名该批次，等待审核', 'success')
            return redirect(url_for('student.student_dashboard'))
        else:
            flash('您已报名该批次', 'warning')
            return redirect(url_for('student.student_dashboard'))

    student = Student(
        user_id=current_user.id,
        batch_id=batch_id,
        name=profile.name,
        id_number=profile.id_number,
        phone=profile.phone
    )
    db.session.add(student)
    db.session.commit()
    flash('报名成功，等待审核', 'success')
    return redirect(url_for('student.student_dashboard'))


@student_bp.route('/student/search_classes')
@login_required
@role_required('student')
def search_classes():
    keyword = request.args.get('keyword', '').strip()
    dept_id = request.args.get('dept_id', '').strip()
    current_class_id = None
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if profile:
        current_class_id = profile.class_id

    query = ClassGroup.query.filter(ClassGroup.is_active == True, ClassGroup.is_graduated == False)

    if current_class_id:
        query = query.filter(ClassGroup.id != current_class_id)

    if dept_id and dept_id.isdigit():
        query = query.filter(ClassGroup.department_id == int(dept_id))

    if keyword:
        query = query.filter(
            (ClassGroup.name.contains(keyword)) | (ClassGroup.class_no.contains(keyword))
        )

    classes = query.order_by(ClassGroup.name).limit(7).all()
    results = [{'id': c.id, 'display': f'{c.name} ({c.class_no})'} for c in classes]
    return jsonify(results)


@student_bp.route('/student/request_transfer', methods=['POST'])
@login_required
@role_required('student')
def request_transfer():
    profile = UserProfile.query.filter_by(user_id=current_user.id).first()
    if not profile:
        flash('请先完善个人档案', 'danger')
        return redirect(url_for('student.student_dashboard'))

    now = datetime.now()
    approved_active = Student.query.filter(
        Student.user_id == current_user.id,
        Student.status == 'approved',
        Student.batch.has(ExamBatch.end_time >= now)
    ).first()

    if approved_active:
        flash('您有已通过且仍在有效期内的报名批次，无法申请转班', 'danger')
        return redirect(url_for('student.student_dashboard'))

    if profile.transfer_status == 'pending':
        flash('您已有一个转班申请正在审核中', 'warning')
        return redirect(url_for('student.student_dashboard'))

    new_class_id = request.form.get('new_class_id')
    if not new_class_id:
        flash('请选择目标班级', 'danger')
        return redirect(url_for('student.student_dashboard'))

    target_class = ClassGroup.query.get(int(new_class_id))
    if not target_class:
        flash('目标班级不存在', 'danger')
        return redirect(url_for('student.student_dashboard'))

    profile.transfer_class_id = int(new_class_id)
    profile.transfer_status = 'pending'
    db.session.commit()
    flash('转班申请已提交，请等待审核', 'success')
    return redirect(url_for('student.student_dashboard'))
