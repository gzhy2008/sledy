from app import app
from models import db, User, Student, ExamBatch
from datetime import datetime

with app.test_client() as client:
    # 登录
    client.post('/login', data={'username': 'test', 'password': '123456'})
    # 请求学生仪表盘
    response = client.get('/student/dashboard')
    html = response.data.decode('utf-8')
    # 检查关键字
    if '填写/查看资料' in html:
        print("✅ 页面显示了批次链接")
    elif '没有开放的批次' in html or '没有开放' in html:
        print("❌ 页面提示没有开放批次")
    else:
        print("⚠️ 未知结果，检查 HTML 内容")
        print(html[:500])
