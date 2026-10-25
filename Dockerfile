# syntax=docker/dockerfile:1
# 健身问答助手：单容器（FastAPI 后端 + 静态前端 + 本地 embedding 推理）
# 目标环境：阿里云 2核2G，CPU 推理（torch 用官方 CPU 轮子，不带 CUDA，省约 2GB 镜像体积）
FROM python:3.11-slim

# PYTHONUNBUFFERED：日志实时输出；PYTHONDONTWRITEBYTECODE：不向挂载卷写 .pyc
# HF_HUB_OFFLINE：embedding 模型从本地路径加载，禁用一切 HF 网络请求
# ANONYMIZED_TELEMETRY：关闭 ChromaDB 匿名遥测，减少无谓外联
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    ANONYMIZED_TELEMETRY=False

WORKDIR /app

# 系统依赖：gcc 兜底个别 sdist 包的本地编译；curl 供 healthcheck 使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc curl \
    && rm -rf /var/lib/apt/lists/*

# 先只拷依赖清单安装：改代码时命中层缓存，不重装数 GB 依赖
# 注意：torch==2.14.1+cpu 仅在 PyTorch 官方 CPU 源发布（PyPI 无 +cpu 后缀版本），必须加 extra-index-url
COPY requirements.txt ./
RUN pip install --no-cache-dir \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    -r requirements.txt

# 拷贝业务代码；models/ 与 data/ 被 .dockerignore 排除，运行时由 compose 卷挂载注入
COPY backend/ backend/
COPY frontend/ frontend/
COPY docs/ docs/
COPY scripts/ scripts/
COPY evalset.json ./

EXPOSE 8000

# 容器内必须绑 0.0.0.0 才能被端口映射访问；2G 内存只跑单 worker（多 worker = 多份 torch + 模型常驻）
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
