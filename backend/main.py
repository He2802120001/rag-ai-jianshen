"""FastAPI 入口：暴露 POST /ask 与 GET /health，仅调用核心 RAG 链路。"""

from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from backend import rag

app = FastAPI(title="Fitness QA Agent")


class AskRequest(BaseModel):
    question: str


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


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> dict:
    result = rag.answer(req.question)
    return result
