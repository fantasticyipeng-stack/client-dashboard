# Gunicorn config: 過濾 /health 的 access log，避免 log 被健康檢查洗版
import logging


class NoHealthAccessFilter(logging.Filter):
    def filter(self, record):
        try:
            return "/health" not in (record.getMessage() or "")
        except Exception:
            return True


def post_fork(server, worker):
    log = logging.getLogger("gunicorn.access")
    for h in log.handlers:
        h.addFilter(NoHealthAccessFilter())
