"""
共享工具模块：所有 Blueprint 共用的辅助函数。
使用 Flask current_app 代理避免循环导入。
"""
import os
import re
import time
import random
import magic
import logging
from collections import defaultdict
from flask import current_app, session, request
from flask_login import current_user
from flask_mail import Message
from PIL import Image
from models import db, Student, UserProfile, HeadTeacher, ClassGroup
from services import ROLE_NAMES, mask_email


# ===================== 速率限制 =====================

_login_attempts = defaultdict(list)
_verify_code_attempts = defaultdict(list)


def _cleanup_old_attempts(store, key, window_seconds):
    now = time.time()
    store[key] = [t for t in store[key] if now - t < window_seconds]


def check_rate_limit(store, key, max_attempts, window_seconds):
    _cleanup_old_attempts(store, key, window_seconds)
    if len(store[key]) >= max_attempts:
        oldest = store[key][0]
        retry_after = int(window_seconds - (time.time() - oldest))
        return False, max(retry_after, 1)
    store[key].append(time.time())
    return True, 0


# ===================== 文件校验 =====================

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in current_app.config['ALLOWED_EXTENSIONS']


def validate_image(file, field_name, max_size_mb=2, exact_size=None):
    """校验图片文件，仅允许 JPG/PNG，可选固定尺寸"""
    if not file or file.filename == '':
        return None
    if not allowed_file(file.filename):
        return f'{field_name}仅支持 JPG/PNG 格式'
    file.seek(0)
    mime = magic.from_buffer(file.read(1024), mime=True)
    file.seek(0)
    if mime not in ['image/jpeg', 'image/png']:
        return f'{field_name}格式不正确，请上传 JPG/PNG 图片'
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > max_size_mb * 1024 * 1024:
        return f'{field_name}大小不能超过{max_size_mb}MB'
    if exact_size:
        try:
            img = Image.open(file)
            if img.size != exact_size:
                return f'{field_name}尺寸需为{exact_size[0]}x{exact_size[1]}像素'
        except Exception:
            return f'无法识别{field_name}文件'
    file.seek(0)
    return None


def validate_other_file(file):
    """校验其他资料：仅允许 PDF 或 RAR，最大 5MB"""
    if not file or file.filename == '':
        return None
    ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
    if ext not in ('pdf', 'rar'):
        return '其他资料仅支持 PDF/RAR 格式'
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > 5 * 1024 * 1024:
        return '其他资料大小不能超过5MB'
    return None


def validate_photo(file):
    if not file or file.filename == '':
        return '请上传证件照'
    if not allowed_file(file.filename):
        return '照片格式仅限 JPG/PNG'
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > 200 * 1024:
        return '照片大小不能超过200KB'
    try:
        img = Image.open(file)
        if img.size != (295, 413):
            return f'照片尺寸需为295x413像素，当前为{img.size[0]}x{img.size[1]}'
        if img.format not in ['JPEG', 'PNG']:
            return '照片实际格式不支持，请上传JPG或PNG'
    except Exception:
        return '无法识别照片文件，请确认文件完好'
    file.seek(0)
    return None


def validate_pdf_or_image(file, field_name, max_size_mb=2):
    if not file or file.filename == '':
        return None
    if not allowed_file(file.filename):
        return f'{field_name}格式仅限 JPG/PNG/PDF'
    file.seek(0, os.SEEK_END)
    size = file.tell()
    file.seek(0)
    if size > max_size_mb * 1024 * 1024:
        return f'{field_name}大小不能超过{max_size_mb}MB'
    mime = magic.from_buffer(file.read(1024), mime=True)
    file.seek(0)
    if mime not in ['image/jpeg', 'image/png', 'application/pdf']:
        return f'{field_name}文件类型不支持'
    return None


# ===================== 文件保存 =====================

def save_file(file, id_number, suffix):
    ext = file.filename.rsplit('.', 1)[1].lower()
    if suffix:
        filename = f"{id_number}_{suffix}.{ext}"
    else:
        filename = f"{id_number}.{ext}"
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)
    return filename


# ===================== 邮件服务 =====================

def send_verify_code(email):
    last_time = session.get('send_code_time', 0)
    if time.time() - last_time < 60:
        return False, '发送过于频繁，请60秒后再试'
    code = ''.join(random.choices('0123456789', k=6))
    session['email_verify_code'] = code
    session['email_verify_code_time'] = time.time()
    session['send_code_time'] = time.time()
    session['email_verify_target'] = email
    mail = current_app.extensions['mail']
    msg = Message('技能认定资料收集系统 - 邮箱验证码', recipients=[email])
    msg.charset = 'utf-8'
    msg.body = f'您的验证码是：{code}，有效期5分钟，请勿泄露。'
    try:
        mail.send(msg)
        return True, '验证码已发送，请查收邮件'
    except Exception as e:
        current_app.logger.error(f'邮件发送失败：{e}')
        return False, '邮件发送失败，请稍后再试'


def send_credentials_notification(operator, target_name, target_username, target_role, target_password, action='创建'):
    """
    将新建/重置的账户凭证存入 session 并尝试通过邮件发送给操作者留底。
    """
    role_display = ROLE_NAMES.get(target_role, target_role)

    session['last_credential'] = {
        'name': target_name,
        'username': target_username,
        'role': role_display,
        'password': target_password,
        'action': action,
        'time': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
    }

    operator_email = operator.email if operator.email and '@' in operator.email else None
    if not operator_email:
        return False, '操作者未设置邮箱，无法发送凭证邮件。凭证已暂存，可在页面顶部查看。'

    mail = current_app.extensions['mail']
    try:
        now_str = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
        msg = Message(
            f'技能认定资料收集系统 - 账户凭证留底（{action}{role_display}）',
            recipients=[operator_email]
        )
        msg.charset = 'utf-8'
        msg.body = (
            f"您好，{operator.username}：\n\n"
            f"您已{action}了以下用户账号，请妥善保管此凭证信息：\n\n"
            f"　　姓名：{target_name}\n"
            f"　　角色：{role_display}\n"
            f"　　用户名：{target_username}\n"
            f"　　{'初始' if action == '创建' else '新'}密码：{target_password}\n\n"
            f"操作时间：{now_str}\n\n"
            f"请将此凭证告知对应用户，并提醒其尽快登录修改密码。\n\n"
            f"—— 技能认定资料收集系统"
        )
        mail.send(msg)
        return True, '凭证邮件已发送，凭证也已暂存于当前页面。'
    except Exception as e:
        current_app.logger.error(f'凭证邮件发送失败：{e}')
        return False, f'凭证邮件发送失败：{e}。凭证已暂存，可在页面顶部查看。'


def revoke_student_registrations_and_notify(profile):
    """
    撤销该学生所有有效报名（pending 或 approved），并发送通知邮件。
    返回撤销的批次名称列表。
    """
    revoked_batches = []
    active_regs = Student.query.filter(
        Student.user_id == profile.user_id,
        Student.status.in_(['pending', 'approved'])
    ).all()

    for reg in active_regs:
        reg.status = 'withdrawn'
        revoked_batches.append(reg.batch.batch_name)

    if revoked_batches:
        db.session.flush()

        try:
            user = profile.user
            if user.email and '@' in user.email:
                mail = current_app.extensions['mail']
                msg = Message('技能认定资料收集系统 - 档案重置通知', recipients=[user.email])
                msg.charset = 'utf-8'
                batch_list = '、'.join(revoked_batches)
                msg.body = (
                    f"同学 {profile.name}，您的档案已被管理员重置为待审核状态。\n"
                    f"您已报名的以下批次已被撤销：{batch_list}\n\n"
                    f"请登录系统重新编辑档案并提交，审核通过后可重新报名批次。\n"
                    f"如有疑问，请联系管理员。"
                )
                mail.send(msg)
        except Exception as e:
            current_app.logger.error(f'撤销报名通知邮件发送失败：{e}')

    return revoked_batches


# ===================== 学生通知 =====================

def notify_student(user, title, body):
    """统一的学生通知邮件：user需有有效邮箱，失败仅记录日志不抛出异常。返回True/False"""
    if not user.email or '@' not in user.email:
        current_app.logger.info(f'通知跳过(无邮箱): user={user.username}')
        return False
    mail = current_app.extensions['mail']
    try:
        msg = Message(title, recipients=[user.email])
        msg.charset = 'utf-8'
        msg.body = body + '\n\n—— 技能认定资料收集系统'
        mail.send(msg)
        current_app.logger.info(f'通知邮件已发送: user={user.username}, to={user.email}, title={title}')
        return True
    except Exception as e:
        current_app.logger.error(f'通知邮件发送失败({user.username}): {e}')
        return False


# ===================== 权限与审计 =====================

def get_admin_department_ids():
    """返回当前业务管理员可管理的系部ID列表。若为全局管理员或非admin角色，返回None表示无限制"""
    if current_user.role == 'admin' and hasattr(current_user, 'admin_profile'):
        ap = current_user.admin_profile
        if ap and not ap.is_global:
            return [d.id for d in ap.departments]
    return None


def audit_log(action, target_desc, detail=None):
    """敏感操作审计日志"""
    ip = request.remote_addr if request else 'N/A'
    operator = f"{current_user.username}({current_user.role})" if current_user.is_authenticated else 'anonymous'
    msg = f"[AUDIT] op={operator} ip={ip} action={action} target={target_desc}"
    if detail:
        msg += f" | {detail}"
    audit_logger = logging.getLogger('audit')
    audit_logger.info(msg)


def notify_class_students(cls, batch, action='add'):
    """班级变更后通知学生和班主任"""
    mail = current_app.extensions.get('mail')
    if not mail:
        return False, '邮件服务未配置'
    action_text = '新增为面向班级' if action == 'add' else '已移除'
    batch_info = batch.display_title if batch.display_title else batch.batch_name
    subject = '技能认定资料收集系统 - 批次班级变更通知'
    total_sent = 0

    # 1. 通知班主任
    teacher = HeadTeacher.query.filter(
        HeadTeacher.classes.any(ClassGroup.id == cls.id)
    ).first()
    if teacher and teacher.user and teacher.user.email and '@' in teacher.user.email:
        try:
            body = f'''{teacher.name} 老师您好：

批次「{batch_info}」已将您管理的班级「{cls.name}」{action_text}。
请及时通知班级学生登录系统查看并完成相关操作。

—— 技能认定资料收集系统'''
            msg = Message(subject, recipients=[teacher.user.email], body=body)
            mail.send(msg)
            total_sent += 1
        except Exception as e:
            current_app.logger.error(f'班主任通知邮件发送失败: {e}')

    # 2. 通知班级内已报名的学生
    students = Student.query.filter_by(batch_id=batch.id).join(
        UserProfile, UserProfile.user_id == Student.user_id
    ).filter(UserProfile.class_id == cls.id).all()
    sc = 0
    for student in students:
        user = student.user
        if user and user.email and '@' in user.email:
            try:
                body = f'''同学你好：

批次「{batch_info}」已将你所在的班级「{cls.name}」{action_text}。
请登录系统查看相关变动。如有疑问请联系班主任。

（你能收到此通知，是因为批次选择班级时你已在班级内部）

—— 技能认定资料收集系统'''
                msg = Message(subject, recipients=[user.email], body=body)
                mail.send(msg)
                sc += 1
            except Exception as e:
                current_app.logger.error(f'班级变更通知邮件发送失败: {e}')
    total_sent += sc
    if not students and total_sent > 0:
        return True, '已通知班主任（班级暂无已报名学生）'
    return True, f'已发送 {total_sent} 封通知邮件（含班主任）'
