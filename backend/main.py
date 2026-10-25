"""FastAPI 入口：暴露 POST /ask 与 GET /health，仅调用核心 RAG 链路。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend import rag
from backend.config import ADMIN_TOKEN, DEEPSEEK_API_KEY, DOCS_DIR, UPLOADS_DIR_NAME
from backend.logger import get_logger

# 全局业务日志：控制台 + logs/app.log 双写（10MB×3 轮转），格式「时间 - 级别 - 模块名 - 消息」
logger = get_logger(__name__)

APP_VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动自检（护盾 3）：关键环境变量缺失 / 默认密码时打严重告警。

    DEEPSEEK_API_KEY 缺失时 config 导入即抛 KeyError（fail-fast），
    这里的兜底检查防止未来 config 改为宽松读取后退化成静默故障。
    """
    if not DEEPSEEK_API_KEY:
        logger.critical("DEEPSEEK_API_KEY 未配置：LLM 问答与建库将不可用！")
    if ADMIN_TOKEN == "fitness-admin":
        logger.warning("WARNING: 正在使用默认管理员密码，请立即修改！")
    yield


app = FastAPI(title="Fitness QA Agent", version=APP_VERSION, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """安全响应头护盾（护盾 2）：全站注入，含静态文件与 SSE 流式响应。

    - nosniff：禁止浏览器猜测 MIME 类型，防恶意上传被当脚本执行
    - DENY：禁止被任何站点 iframe 嵌入，防点击劫持
    - XSS-Protection：启用浏览器内建反射型 XSS 拦截（旧浏览器兜底）
    """
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response

# ---- 接口限流（SlowAPI，按客户端 IP 的内存固定窗口计数）----
# 仅装饰 /ask 与 /ask_stream：静态页面、/health、管理接口均不受限
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """触发限流：429 + 友好中文提示（detail 可被前端直接展示）。"""
    logger.warning(
        "[rate-limit] IP=%s path=%s 触发限流（%s）",
        get_remote_address(request),
        request.url.path,
        exc.detail,
    )
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(getattr(exc, "retry_after", "60"))},
        content={"detail": "提问太频繁啦，休息一分钟再来吧～"},
    )

# 动态上传文档的落盘目录与限制
UPLOADS_DIR = DOCS_DIR / UPLOADS_DIR_NAME
ALLOWED_EXTENSIONS = (".md", ".txt")
MAX_UPLOAD_BYTES = 500 * 1024  # 上传内容上限 500KB（按 UTF-8 字节计），防超大文件拖垮服务

# 提问长度上限：超长问题直接 400，不进检索与 LLM 链路
MAX_QUESTION_CHARS = 1000

# 伪科学/违规推广类敏感词：命中即拒绝入库（数据投毒防护的第一道闸）
SENSITIVE_WORDS = (
    "包治百病", "停药", "绝对安全", "根治", "包治", "神药",
    "百分百有效", "无副作用", "三天见效", "抗癌", "保健品治疗",
)


class UploadRequest(BaseModel):
    filename: str
    content: str


def _check_admin_token(x_admin_token: Optional[str]) -> None:
    """管理接口鉴权：请求头 X-Admin-Token 必须与配置一致。"""
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="无权限：Admin Token 缺失或不正确")


def _check_question(question: str) -> None:
    """OOM 护盾：超长提问直接 400 拦截，不进检索与 LLM 链路。"""
    if len(question) > MAX_QUESTION_CHARS:
        logger.warning("[ask] 拦截：问题超过 %d 字（实际 %d 字），已拒绝", MAX_QUESTION_CHARS, len(question))
        raise HTTPException(status_code=400, detail="问题太长，请精简后提问")


class AskRequest(BaseModel):
    question: str
    history: List[dict] = []  # 可选：最近几轮的对话 [[{role, content}, ...]]，用于多轮上下文


class Source(BaseModel):
    text: str
    source: str


class AskResponse(BaseModel):
    answer: str
    sources: List[Source]
    refused: bool
    clarify: bool
    category: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


def _hist_summary(history: list[dict] | None) -> str:
    """把 history 压缩成『角色(字数)』摘要，用于审计日志。"""
    return "; ".join(
        f"{h.get('role')}({len(h.get('content') or '')}字)" for h in (history or [])
    )


def _audit_log(fmt: str, *args) -> None:
    """请求审计日志：logging 本身不会向调用方抛错，这里再兜一层，
    确保审计在任何情况下（格式化异常/编码问题等）都不影响 /ask 主链路。"""
    try:
        logger.info(fmt, *args)
    except Exception as e:
        logger.warning("发生降级：审计日志写入失败: %s", e)


@app.post("/ask", response_model=AskResponse)
@limiter.limit("10/minute")
def ask(request: Request, req: AskRequest) -> dict:
    _check_question(req.question)  # OOM 护盾：超长提问在审计与检索前拦截
    # 请求审计日志：记录原始 question 与 history 摘要，便于排查
    # 「前端到底传了什么」「分类结果是什么」类问题
    _audit_log("[ask] question=%r history=[%s]", req.question, _hist_summary(req.history))
    result = rag.answer(req.question, history=req.history or None)
    _audit_log(
        "[ask] -> category=%s refused=%s clarify=%s sources=%d answer_len=%d",
        result.get("category"),
        result.get("refused"),
        result.get("clarify"),
        len(result.get("sources", [])),
        len(result.get("answer", "")),
    )
    return result


@app.post("/ask_stream")
@limiter.limit("10/minute")
def ask_stream(request: Request, req: AskRequest) -> StreamingResponse:
    """流式问答：SSE 事件流。事件协议见 rag.answer_stream 文档字符串。

    保留 POST /ask 不变（非流式），供评估脚本继续使用。
    """
    _check_question(req.question)  # OOM 护盾：超长提问在审计与检索前拦截
    _audit_log("[ask_stream] question=%r history=[%s]", req.question, _hist_summary(req.history))
    return StreamingResponse(
        rag.answer_stream(req.question, history=req.history or None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@app.post("/upload")
def upload(
    req: UploadRequest, x_admin_token: Optional[str] = Header(default=None)
) -> dict:
    """动态知识库：上传新文档（JSON：filename + content），鉴权 + 敏感词过滤后增量入库。"""
    _check_admin_token(x_admin_token)

    # 文件名安全处理：仅取文件名部分（防路径穿越），校验扩展名
    name = Path(req.filename.replace("\\", "/")).name
    if not name.lower().endswith(ALLOWED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="仅支持 .md 或 .txt 文件")
    # OOM 护盾：按 UTF-8 字节计 500KB 上限，超限直接 413，不落盘不入库
    content_bytes = len(req.content.encode("utf-8"))
    if content_bytes > MAX_UPLOAD_BYTES:
        logger.warning(
            "[upload] 拒绝入库：内容超过 500KB 上限（%d 字节）filename=%r", content_bytes, name
        )
        raise HTTPException(status_code=413, detail="文件过大（超过 500KB 上限）")

    # 敏感词过滤（内容投毒防护）：命中即拒绝，不落盘不入库
    hit = next((w for w in SENSITIVE_WORDS if w in req.content or w in name), None)
    if hit:
        _audit_log("[upload] 拒绝入库：命中敏感词 question=%r", name)
        raise HTTPException(status_code=400, detail=f"内容包含违规词“{hit}”，已拒绝入库")

    # 落盘到 docs/uploads/ 并增量写入 ChromaDB
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    save_path = UPLOADS_DIR / name
    save_path.write_text(req.content, encoding="utf-8")
    stats = rag.add_document(save_path)
    _audit_log(
        "[upload] 入库成功 source=%r added=%d", stats["source"], stats["added"]
    )
    return {"ok": True, "source": stats["source"], "added": stats["added"]}


@app.get("/documents")
def documents(x_admin_token: Optional[str] = Header(default=None)) -> dict:
    """列出知识库当前所有不重复的文档来源名（需管理员鉴权）。"""
    _check_admin_token(x_admin_token)
    return {"documents": rag.list_sources()}


# 静态文件挂载必须放在所有 API 路由之后，否则 /ask、/health 会被静态路由拦截
PROJECT_ROOT = Path(__file__).resolve().parent.parent
app.mount(
    "/",
    StaticFiles(directory=str(PROJECT_ROOT / "frontend"), html=True),
    name="frontend",
)
