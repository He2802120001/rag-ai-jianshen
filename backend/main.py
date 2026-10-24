"""FastAPI 入口：暴露 POST /ask 与 GET /health，仅调用核心 RAG 链路。"""

from __future__ import annotations

import logging
from typing import List, Optional

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import rag

# 挂到 uvicorn 的日志配置上，输出会进入 uvicorn_stderr.log
logger = logging.getLogger("uvicorn.error")

app = FastAPI(title="Fitness QA Agent")


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
    except Exception:
        pass


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> dict:
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
def ask_stream(req: AskRequest) -> StreamingResponse:
    """流式问答：SSE 事件流。事件协议见 rag.answer_stream 文档字符串。

    保留 POST /ask 不变（非流式），供评估脚本继续使用。
    """
    _audit_log("[ask_stream] question=%r history=[%s]", req.question, _hist_summary(req.history))
    return StreamingResponse(
        rag.answer_stream(req.question, history=req.history or None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


# 静态文件挂载必须放在所有 API 路由之后，否则 /ask、/health 会被静态路由拦截
PROJECT_ROOT = Path(__file__).resolve().parent.parent
app.mount(
    "/",
    StaticFiles(directory=str(PROJECT_ROOT / "frontend"), html=True),
    name="frontend",
)
