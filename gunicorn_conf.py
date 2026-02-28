# Gunicorn config: 不記錄 /health 的 access log，避免被 Render 健康檢查洗版
import logging

# 自訂 Logger：/health 請求完全不寫入 access log
try:
    from gunicorn.glogging import Logger
except ImportError:
    Logger = object


class NoHealthLogger(Logger):
    def access(self, resp, req, environ, request_time):
        if environ.get("PATH_INFO") == "/health":
            return
        super().access(resp, req, environ, request_time)


# 讓 gunicorn 使用自訂 logger
logger_class = NoHealthLogger
