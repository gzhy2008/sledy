"""
服务层：纯工具函数与常量，不依赖 Flask 请求上下文或配置。
"""
import re
import random
import string

# ===================== 角色中文名常量 =====================
ROLE_NAMES = {
    'headteacher': '班主任',
    'admin': '业务管理员',
    'student': '学生',
    'super_admin': '超级管理员'
}

# ===================== 密码与验证 =====================

def generate_random_password(length=12):
    """生成随机密码：包含大小写字母和数字"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choices(chars, k=length))


def validate_phone(phone):
    """校验手机号格式：1开头的11位数字，第二位不能是0/1/2"""
    return bool(re.match(r'^1[3-9]\d{9}$', phone))


def validate_password_strength(password):
    """
    校验密码强度：至少8位，必须包含大写字母、小写字母和数字。
    返回 (is_valid, error_message)
    """
    if len(password) < 8:
        return False, '密码至少需要8位字符'
    if not re.search(r'[A-Z]', password):
        return False, '密码必须包含至少一个大写字母'
    if not re.search(r'[a-z]', password):
        return False, '密码必须包含至少一个小写字母'
    if not re.search(r'\d', password):
        return False, '密码必须包含至少一个数字'
    return True, ''

# ===================== 邮箱脱敏 =====================

def mask_email(email):
    if '@' not in email:
        return email
    local, domain = email.split('@')
    if len(local) > 2:
        masked_local = local[:2] + '***'
    else:
        masked_local = local[0] + '***'
    domain_parts = domain.split('.')
    if len(domain_parts) > 1:
        masked_domain = domain_parts[0][0] + '***' + '.' + domain_parts[-1]
    else:
        masked_domain = domain[0] + '***'
    return masked_local + '@' + masked_domain


def validate_strong_password(password):
    """超管专用：14位以上，含大写、小写、数字、特殊字符"""
    if len(password) < 14:
        return False, '密码至少需要14位字符'
    if not re.search(r'[A-Z]', password):
        return False, '密码必须包含至少一个大写字母'
    if not re.search(r'[a-z]', password):
        return False, '密码必须包含至少一个小写字母'
    if not re.search(r'\d', password):
        return False, '密码必须包含至少一个数字'
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};:\'",.<>/?\\|`~]', password):
        return False, '密码必须包含至少一个特殊字符'
    return True, ''
