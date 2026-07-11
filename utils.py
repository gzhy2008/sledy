import random
import time
import string
import re

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from flask import session, abort, redirect, url_for, flash
from functools import wraps
from flask_login import current_user

def generate_captcha_text(length=4):
    """生成随机数字验证码"""
    return ''.join(random.choices(string.digits, k=length))

def create_captcha_image(text, width=120, height=40):

    image = Image.new('RGB', (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    
    # 尝试使用系统等宽字体，如果不存在则回退到默认字体
    try:
        # Linux 常见路径，Ubuntu 通常有 DejaVuSansMono
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf', 28)
    except IOError:
        try:
            font = ImageFont.truetype('/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf', 28)
        except IOError:
            font = ImageFont.load_default()  # 默认字体太小，不推荐，但作为最后的回退
    
    # 绘制每个字符，添加随机偏移但保持可读
    for i, char in enumerate(text):
        x = 10 + i * 25 + random.randint(-2, 2)
        y = random.randint(3, 8)
        draw.text((x, y), char, fill=(0, 0, 0), font=font)
    
    # 轻微干扰：2条细线，少量噪点
    for _ in range(2):
        x1 = random.randint(0, width)
        y1 = random.randint(0, height)
        x2 = random.randint(0, width)
        y2 = random.randint(0, height)
        draw.line([(x1, y1), (x2, y2)], fill=(150, 150, 150), width=1)
    
    for _ in range(20):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.point((x, y), fill=(random.randint(50, 200), random.randint(50, 200), random.randint(50, 200)))
    
    # 不模糊，保持清晰
    buf = BytesIO()
    image.save(buf, 'jpeg', quality=90)
    buf.seek(0)
    return buf

# 权限装饰器
def role_required(*roles):
    """限制访问特定角色"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for('auth.login'))
            if current_user.role not in roles:
                flash('您没有权限访问该页面', 'danger')
                return redirect(url_for('auth.dashboard'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def validate_id_number_checksum(id_num):
    """
    校验身份证号格式及最后一位校验码（GB 11643-1999）
    返回 True 表示合法，False 表示非法
    """
    if not re.match(r'^\d{17}[\dXx]$', id_num):
        return False
    weights = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
    check_codes = '10X98765432'
    total = sum(int(id_num[i]) * weights[i] for i in range(17))
    expected = check_codes[total % 11]
    return id_num[-1].upper() == expected


