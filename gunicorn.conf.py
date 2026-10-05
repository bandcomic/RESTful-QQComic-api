import os

bind = "127.0.0.1:" + os.environ.get("PORT", "3004")
workers = int(os.environ.get("WEB_WORKERS", "2"))
worker_class = "gthread"
threads = int(os.environ.get("WEB_THREADS", "2"))
timeout = 60
graceful_timeout = 30
max_requests = int(os.environ.get("WEB_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.environ.get("WEB_MAX_REQUESTS_JITTER", "100"))
preload_app = False
accesslog = "-"
errorlog = "-"
