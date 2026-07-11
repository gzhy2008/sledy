from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

# ---------- 用户表 ----------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    email = db.Column(db.String(120), default='')
    email_verified = db.Column(db.Boolean, default=False)
    reset_note = db.Column(db.String(256), default='')
    role = db.Column(db.String(20), nullable=False, default='student')
    is_active = db.Column(db.Boolean, default=True)
    verification_attempts = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    profile = db.relationship('UserProfile', backref='user', uselist=False)
    head_teacher = db.relationship('HeadTeacher', backref='user', uselist=False)
    admin_profile = db.relationship('AdminProfile', backref='user', uselist=False)

    def get_id(self):
        return str(self.id)

# ---------- 系部表 ----------
class Department(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    is_deleted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ---------- 业务管理员资料表 ----------
class AdminProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    name = db.Column(db.String(64), nullable=False)
    id_number = db.Column(db.String(18), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    is_global = db.Column(db.Boolean, default=True)
    # 多对多关系：一个普通管理员可管理多个系部
    departments = db.relationship('Department', secondary='admin_departments', backref='limited_admins')
    
# ---------- 班主任资料表 ----------
class HeadTeacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    name = db.Column(db.String(64), nullable=False)
    id_number = db.Column(db.String(18), unique=True, nullable=False)
    gender = db.Column(db.String(4))
    phone = db.Column(db.String(20))
    # 多对多关联系部
    departments = db.relationship('Department', secondary='teacher_departments', backref='head_teachers')

#--------------班主任与系部多对多关联表 ---------
teacher_departments = db.Table('teacher_departments',
    db.Column('teacher_id', db.Integer, db.ForeignKey('head_teacher.id'), primary_key=True),
    db.Column('department_id', db.Integer, db.ForeignKey('department.id'), primary_key=True)
)

# ---------- 班级表 ----------
class ClassGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    class_no = db.Column(db.String(32), unique=True, nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('department.id'))
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'))
    teacher_id = db.Column(db.Integer, db.ForeignKey('head_teacher.id'))
    is_active = db.Column(db.Boolean, default=True)
    is_graduated = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    department = db.relationship('Department', backref='classes')
    teacher = db.relationship('HeadTeacher', backref='classes')
    students = db.relationship('UserProfile', backref='class_group', lazy='dynamic',
                               foreign_keys='UserProfile.class_id')

#----------- 业务管理员与系部多对多关联表 -------
admin_departments = db.Table('admin_departments',
    db.Column('admin_id', db.Integer, db.ForeignKey('admin_profile.id'), primary_key=True),
    db.Column('department_id', db.Integer, db.ForeignKey('department.id'), primary_key=True)
)

# ---------- 学生个人档案表 ----------
class UserProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), unique=True, nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('class_group.id'))
    name = db.Column(db.String(64), nullable=False)
    gender = db.Column(db.String(4))
    id_number = db.Column(db.String(18), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    department_name = db.Column(db.String(64))
    photo_path = db.Column(db.String(256))
    edu_cert_path = db.Column(db.String(256))
    id_card_front_path = db.Column(db.String(256))
    id_card_back_path = db.Column(db.String(256))
    other_file_path = db.Column(db.String(256))
    status = db.Column(db.String(16), default='pending')
    reject_reason = db.Column(db.String(100), default='')
    transfer_class_id = db.Column(db.Integer, db.ForeignKey('class_group.id'))
    transfer_class_group = db.relationship('ClassGroup', foreign_keys=[transfer_class_id])
    transfer_status = db.Column(db.String(16), default='none')

# ---------- 工种表 ----------
class Skill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False)
    is_locked = db.Column(db.Boolean, default=False)
    is_deleted = db.Column(db.Boolean, default=False)
    issue_number = db.Column(db.Integer, default=0)   # 期数，从0开始，首次创建批次时+1得1

# ---------- 批次与班级多对多关联表 ----------
batch_classes = db.Table('batch_classes',
    db.Column('batch_id', db.Integer, db.ForeignKey('exam_batch.id'), primary_key=True),
    db.Column('class_id', db.Integer, db.ForeignKey('class_group.id'), primary_key=True)
)

# ---------- 认定批次表 ----------
class ExamBatch(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    batch_name = db.Column(db.String(64), unique=True, nullable=False)
    batch_keyword = db.Column(db.String(20), default='')
    skill_id = db.Column(db.Integer, db.ForeignKey('skill.id'), nullable=False)
    skill_level = db.Column(db.String(32), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    work_start_time = db.Column(db.DateTime, nullable=True)
    work_end_time = db.Column(db.DateTime, nullable=True)
    issue_number = db.Column(db.Integer)
    is_archived = db.Column(db.Boolean, default=False)
    archived_at = db.Column(db.DateTime, nullable=True)
    is_locked = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    skill = db.relationship('Skill', backref='batches')
    classes = db.relationship('ClassGroup', secondary='batch_classes', backref='batches')
    
    @property
    def display_title(self):
        parts = ['批次简述:']
        if self.batch_keyword:
            parts.append(self.batch_keyword)
        skill_name = self.skill.name if self.skill else ''
        parts.append(f'第{self.issue_number}期{skill_name}{self.skill_level}认定工作')
        return ''.join(parts)

# ---------- 学生报名记录表 ----------
class Student(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    batch_id = db.Column(db.Integer, db.ForeignKey('exam_batch.id'), nullable=False)
    name = db.Column(db.String(64), nullable=False)
    id_number = db.Column(db.String(18), nullable=False)
    phone = db.Column(db.String(20))
    status = db.Column(db.String(16), default='pending')
    reject_reason = db.Column(db.String(100), default='')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship('User', backref='registrations')
    batch = db.relationship('ExamBatch', backref='students')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'batch_id', name='unique_student_batch'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'id_number': self.id_number,
            'phone': self.phone,
            'batch_name': self.batch.batch_name if self.batch else '',
            'skill_name': self.batch.skill.name if self.batch and self.batch.skill else '',
            'skill_level': self.batch.skill_level if self.batch else '',
            'status': self.status,
            'reject_reason': self.reject_reason,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M')
        }


# ---------- 通知公告表 ----------
class Notice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(128), nullable=False)
    content = db.Column(db.Text, default='')
    importance = db.Column(db.String(8), default='normal')     # normal / important / urgent
    is_published = db.Column(db.Boolean, default=False)
    is_public = db.Column(db.Boolean, default=False)           # 是否向陌生人展示
    is_deleted = db.Column(db.Boolean, default=False)          # 软删除
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------- 站点配置表 (键值对) ----------
class SiteConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(64), unique=True, nullable=False)
    value = db.Column(db.String(256), default='')
