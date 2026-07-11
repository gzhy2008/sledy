"""WSGI 入口，供 gunicorn 启动生产环境"""
from app import app

if __name__ == '__main__':
    app.run()
