"""全局配置：从环境变量读取服务密钥，定义路径与 RAG 参数。"""

import os
from pathlib import Path

# 项目根目录：backend/config.py -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---- 管理后台（动态知识库）----
# 上传/查库接口的鉴权令牌；生产环境务必通过环境变量覆盖默认值
# 默认值单独导出常量，供启动自检比对「是否仍在使用默认密码」
DEFAULT_ADMIN_TOKEN = "fitness-admin"
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", DEFAULT_ADMIN_TOKEN)

# 动态上传文档的存放目录（相对 docs/），目录名即 ChromaDB category
UPLOADS_DIR_NAME = "uploads"

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
# uploads：运行期通过管理后台上传的动态文档，全量重建时一并入库
DOCS_DIR = PROJECT_ROOT / "docs"
DOC_CATEGORIES = ["technique", "nutrition", "injury-prevention", "uploads"]
