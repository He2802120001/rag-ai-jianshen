"""全局配置：从环境变量读取服务密钥，定义路径与 RAG 参数。"""

import os
from pathlib import Path

# 项目根目录：backend/config.py -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---- LLM（DeepSeek，OpenAI SDK）----
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]  # 缺失时显式报错，避免静默失败
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-flash"

# ---- ChromaDB ----
CHROMA_DIR = PROJECT_ROOT / "data" / "chroma_db"
COLLECTION_NAME = "fitness_docs"

# ---- Embedding（本地 sentence-transformers，从本地路径加载，避免依赖外网）----
# 模型已由 ModelScope 下载到模型根下的 models/bge-small-zh-v1.5
EMBEDDING_MODEL_NAME = str(PROJECT_ROOT / "models" / "bge-small-zh-v1.5")

# ---- 文档分块 ----
CHUNK_SIZE = 500
CHUNK_OVERLAP = 50

# ---- 检索 ----
TOP_K = 3

# 知识库文档子目录（相对 docs/）：目录名即 category
DOCS_DIR = PROJECT_ROOT / "docs"
DOC_CATEGORIES = ["technique", "nutrition", "injury-prevention"]
