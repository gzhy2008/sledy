# Gunicorn 生产环境配置
# 启动: gunicorn -c gunicorn.conf.py wsgi:app

import os
import multiprocessing

# 绑定地址
bind = os.environ.get('FLASK_HOST', '0.0.0.0') + ':' + os.environ.get('FLASK_PORT', '5000')

# Worker 进程数 = CPU核数 × 2 + 1 (适用于 I/O 密集型)
workers = multiprocessing.cpu_count() * 2 + 1

# Worker 类型: sync 即可 (Flask 不需要 gevent)
worker_class = 'sync'

# 每个 worker 独立重启时间（秒），防止内存泄漏
max_requests = 10000
max_requests_jitter = 500

# 超时
timeout = 120
graceful_timeout = 30

# 日志
accesslog = '-'
errorlog = '-'
loglevel = 'warning'

# 进程命名
proc_name = 'exam_tool'

# 后台运行
daemon = False
pidfile = os.path.join(os.path.dirname(__file__), 'gunicorn.pid')
