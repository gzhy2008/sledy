import sqlite3
from app import app, db
from models import User, SiteConfig, ExamBatch, Student, UserProfile, ClassGroup, Department, Skill, HeadTeacher, AdminProfile

def migrate():
    # 连接旧的 SQLite 数据库
    sqlite_conn = sqlite3.connect('data.db')
    sqlite_conn.row_factory = sqlite3.Row
    cursor = sqlite_conn.cursor()

    with app.app_context():
        # 1. 在 MySQL 中创建新表
        print("正在 MySQL 中创建表结构...")
        db.create_all()

        # 2. 按依赖关系排序（父表在前，子表在后）
        models = [
            Department, Skill, ClassGroup, User, AdminProfile, HeadTeacher, 
            UserProfile, ExamBatch, SiteConfig, Student
        ]

        for model in models:
            table_name = model.__tablename__
            print(f"正在迁移表: {table_name} ...")
            try:
                cursor.execute(f"SELECT * FROM {table_name}")
                rows = cursor.fetchall()
                
                for row in rows:
                    data = dict(row)
                    obj = model(**data)
                    db.session.add(obj)
                
                db.session.commit()
                print(f"  -> 成功迁移 {len(rows)} 条记录")
            except sqlite3.OperationalError:
                print(f"  -> 旧库中无 {table_name} 表，跳过")
            except Exception as e:
                db.session.rollback()
                print(f"  -> 迁移 {table_name} 失败: {e}")

    sqlite_conn.close()
    print("迁移完成！")

if __name__ == '__main__':
    migrate()
