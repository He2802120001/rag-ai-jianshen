"""全局日志配置：控制台 + logs/app.log 双输出，10MB × 3 份自动轮转。

所有 backend 模块统一通过 get_logger(__name__) 获取 logger：
- 格式：时间 - 级别 - 模块名 - 消息
- handler 挂在 "backend" 父级 logger 上，backend.* 命名空间自动继承；
  propagate 关闭，避免向 uvicorn 根 logger 冒泡造成控制台双份输出
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 日志目录与文件：项目根/logs/app.log（首次使用时自动创建）
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FILE = LOG_DIR / "app.log"

# 格式要求：时间 - 级别 - 模块名 - 消息
LOG_FORMAT = "%(asctime)s - %(levelname)s - %(name)s - %(message)s"

# 业务日志统一的父级 logger（backend.main / backend.rag 等自动挂到它下面）
_ROOT_NAME = "backend"

# 单文件上限 10MB，保留 3 份历史（app.log.1 / .2 / .3），最多占 40MB 磁盘
_MAX_BYTES = 10 * 1024 * 1024
_BACKUP_COUNT = 3


def _setup() -> None:
    """幂等初始化：重复调用（多次 import / 热重载）不会叠加 handler。"""
    root = logging.getLogger(_ROOT_NAME)
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    root.propagate = False
    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)


def get_logger(name: str) -> logging.Logger:
    """返回业务 logger；name 一般传 __name__（如 backend.main）。"""
    _setup()
    return logging.getLogger(name)
