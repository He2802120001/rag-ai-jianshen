"""核心 RAG 链路：文档加载、分块、建库、检索、生成回答。

只实现单一 RAG 链路，不做意图分类 / 安全拒答 / 追问 / 前端 / 评估。
"""

from __future__ import annotations

from pathlib import Path

import chromadb
from openai import OpenAI
from sentence_transformers import SentenceTransformer

from backend.classifier import classify, has_domain_entity
from backend.config import (
    CHROMA_DIR,
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    COLLECTION_NAME,
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DOCS_DIR,
    DOC_CATEGORIES,
    EMBEDDING_MODEL_NAME,
    TOP_K,
)

# 检索相似度阈值：低于该相似度视为信息不足，追问而非硬答
# ChromaDB cosine 距离(d) ∈ [0,2]，相似度取 1-d；阈值 0.5 对应 d <= 0.5
SIMILARITY_THRESHOLD = 0.5

# refuse / clarify 固定话术（保持教练口吻，避免机器腔）
REFUSE_REPLY = (
    "这个问题涉及伤病诊断和医疗建议，超出了我能安全回答的范围，我不能给你下结论。"
    "别硬扛，建议尽快咨询医生或专业康复师，拿到明确结论再回来调整训练。\n"
    "训练动作、营养安排上的问题，我随时都在。"
)
CLARIFY_REPLY = (
    "我想帮你答得更准一些：能补充一下具体是哪个动作、哪方面的问题"
    "（动作技术 / 营养 / 伤病预防），或者你目前的训练基础吗？"
)

# System Prompt：身份、引用规则与表达风格。「去机器化」的关键约束放在这里，
# 以独立 system 消息下发的约束力远强于混在 user 内容里
SYSTEM_PROMPT = """你是一位专业、亲切的健身教练，通过问答助手为学员解答训练与营养问题。你只能依据提供的文档内容作答。

引用与措辞规则（必须严格遵守）：
- 绝对禁止出现"片段1""片段2""片段3"或"依据片段…"这类字眼，任何情况下都不允许。
- 每段参考文档的名称已用【文档：《名称》】标出。需要说明依据时，请使用自然表达，例如："根据《文档名》的资料显示，……"。
- 只能引用列出的文档名，不要编造或改写文档名；文档中没有的信息不要编造。
- 仅当所有文档内容都与问题明显无关时，才直说当前资料覆盖不到、无法给出可靠建议，
  并请学员换个问法；只要有部分相关，就基于相关部分作答，不要因为不完整而拒绝。

表达风格：
- 像真人教练面对学员一样说话：亲切、专业、有温度，直接称呼"你"。
- 回答简洁口语化，可以适当分点，但不要机械罗列；结尾可以给一句鼓励或提醒。
- 不要输出任何形式的片段编号或"参考来源 1/2/3"式的列表。"""

_MD_MARKERS = ("# ", "## ", "### ", "> ")


def _chunk_text(text: str) -> list[str]:
    """按段落贪心分块：段落到界后每块不超过 chunk_size，块间重叠 chunk_overlap 字。"""
    if not text:
        return []
    chunks: list[str] = []
    current = ""
    # 以空行切分段落，合并成可读性好的连续块
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    for para in paragraphs:
        # 若单段就超过块上限，按字符硬切（保留重叠）
        if len(para) > CHUNK_SIZE:
            # 先落盘当前缓冲
            if current:
                chunks.append(current)
                current = ""
            i = 0
            while i < len(para):
                chunks.append(para[i : i + CHUNK_SIZE])
                i += CHUNK_SIZE - CHUNK_OVERLAP
            continue
        if current and len(current) + len(para) + 1 > CHUNK_SIZE:
            chunks.append(current)
            # 重叠：取当前块尾部 chunk_overlap 字作为下一块开头
            current = current[-CHUNK_OVERLAP:] if len(current) >= CHUNK_OVERLAP else ""
        current = (current + "\n\n" + para) if current else para
    if current:
        chunks.append(current)
    return chunks


def load_documents(docs_dir: Path = DOCS_DIR) -> list[dict]:
    """遍历 docs 下各分类目录，读取 .md 文件并按段分块。

    返回 list[dict]：每块含 text / source（相对项目根路径）/ category（目录名）。
    """
    docs: list[dict] = []
    for category in DOC_CATEGORIES:
        category_dir = docs_dir / category
        if not category_dir.is_dir():
            continue
        for md_path in sorted(category_dir.glob("*.md")):
            content = md_path.read_text(encoding="utf-8")
            for idx, chunk in enumerate(_chunk_text(content)):
                # 跳过只有标题标记的噪声块
                stripped = chunk.strip()
                if not stripped or stripped in _MD_MARKERS:
                    continue
                docs.append(
                    {
                        "text": stripped,
                        "source": str(md_path.relative_to(docs_dir.parent)),
                        "id": f"{category}:{md_path.stem}:{idx}",
                        "category": category,
                    }
                )
    return docs


def _get_embedder() -> SentenceTransformer:
    """模块级缓存 embedding 模型，避免多次加载。"""
    if not hasattr(_get_embedder, "_model"):
        _get_embedder._model = SentenceTransformer(EMBEDDING_MODEL_NAME)
    return _get_embedder._model


def _embed(texts: list[str]) -> list[list[float]]:
    # bge 系模型建议 normalize；collection 用 cosine 距离，二者配合最佳
    emb = _get_embedder().encode(texts, normalize_embeddings=True)
    return emb.tolist()


def _get_collection():
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return client.get_or_create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )


def build_index() -> dict:
    """加载文档、向量化并写入 ChromaDB。幂等：先删 collection 再重建。"""
    docs = load_documents()
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    # 先删后建，保证重复调用不产生重复向量
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    collection = client.create_collection(
        name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    if not docs:
        return {"added": 0, "chunks": 0, "categories": {}}
    texts = [d["text"] for d in docs]
    ids = [d["id"] for d in docs]
    metadatas = [{"source": d["source"], "category": d["category"]} for d in docs]
    embeddings = _embed(texts)
    collection.add(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)
    cat_count: dict[str, int] = {}
    for d in docs:
        cat_count[d["category"]] = cat_count.get(d["category"], 0) + 1
    return {"added": len(ids), "chunks": len(texts), "categories": cat_count}


def retrieve(query: str, top_k: int = TOP_K, categories: list[str] | None = None) -> list[dict]:
    """编码 query，从 ChromaDB 检索 top_k 片段。

    返回 [{text, source, category, distance, similarity}]。
    categories：若给定，则按类别过滤检索目录（如仅检索 technique）。
    """
    if not query.strip():
        return []
    collection = _get_collection()
    q_emb = _embed([query.strip()])[0]
    where = None
    if categories:
        # ChromaDB where 过滤：category 属于给定集合
        where = {"category": {"$in": list(categories)}}
    result = collection.query(
        query_embeddings=[q_emb],
        n_results=top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    hits: list[dict] = []
    docs = result["documents"][0]
    metas = result["metadatas"][0]
    dists = result["distances"][0]
    for text, meta, dist in zip(docs, metas, dists):
        d = float(dist)
        hits.append(
            {
                "text": text,
                "source": meta.get("source", ""),
                "category": meta.get("category", ""),
                "distance": d,
                "similarity": 1.0 - d,
            }
        )
    return hits


def _merge_context(query: str, history: list[dict] | None) -> str:
    """把最近的历史对话与当前问题合并成一段上下文，用于分类与检索。"""
    if not history:
        return query
    parts = []
    for h in history:
        role = "用户" if h.get("role") == "user" else "助手"
        parts.append(f"{role}：{h.get('content', '')}")
    parts.append(f"用户当前问题：{query}")
    return "\n".join(parts)


def _doc_name(source: str) -> str:
    """从 source 提取文档名（去路径、去 .md 后缀）。如 docs/technique/protein-intake.md → protein-intake。"""
    return Path(source).stem if source else ""


def _build_messages(
    query: str, context: list[dict], history: list[dict] | None = None
) -> list[dict]:
    """组装 system + user 两条消息。

    文档名随上下文以【文档：《名称》】标出，模型据此在回答中按《文档名》引用，
    而不是「片段1 / 片段3」这类编号。
    """
    lines: list[str] = []
    # 历史对话上下文（最近的几轮），帮助结合上文理解当前问题
    if history:
        lines.append("历史对话：")
        for h in history:
            role = "学员" if h.get("role") == "user" else "教练"
            lines.append(f"{role}：{h.get('content', '')}")
        lines.append("")
    lines.append("可参考的文档内容：")
    for hit in context:
        name = _doc_name(hit.get("source", ""))
        lines.append(f"【文档：《{name}》】")
        lines.append(hit["text"])
        lines.append("")
    lines.append(f"学员的当前问题：{query}")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(lines)},
    ]


def answer(query: str, top_k: int = TOP_K, history: list[dict] | None = None) -> dict:
    """RAG 问答入口：先分类，再按类别决定 refuse / clarify / 正常检索生成。

    返回 {"answer", "sources", "category", "refused", "clarify"}。
    - refused=True：拒绝回答（伤病/医疗，固定话术，不检索）
    - clarify=True：追问澄清（信息不足，不检索）
    - 正常：按 category 限定检索目录，相似度不足时追问而非硬答

    history：可选，最近几轮的对话（[{role, content}, ...]），用于多轮上下文。
            检索基于「历史拼接 + 当前问题」的合并上下文；
            分类只看当前问题（历史仅作 LLM 兜底分类参考），避免历史文本中的
            "疼/伤/诊断"等字眼把正常问题连坐误判为 refuse。
    """
    recent = history[-4:] if history else None
    category = classify(query, history=recent)
    merged = _merge_context(query, recent)

    # 1) 应拒答类：不检索、不生成建议
    if category == "refuse":
        return {
            "answer": REFUSE_REPLY,
            "sources": [],
            "category": category,
            "refused": True,
            "clarify": False,
        }

    # 2) 模糊问题：追问澄清
    if category == "clarify":
        return {
            "answer": CLARIFY_REPLY,
            "sources": [],
            "category": category,
            "refused": False,
            "clarify": True,
        }

    # 3) 正常类：按类别目录限定检索（单目录；跨类检索留待后续）
    categories = [category] if category in ("technique", "nutrition", "injury-prevention") else None
    hits = retrieve(merged, top_k=top_k, categories=categories)

    # 相似度不足 → 追问而非硬答。
    # 仅限「没有领域实体词」的问题：实体明确（蛋白质/深蹲/热身…）的问题
    # 即使相似度偏低也直接检索回答，绝不降级为 clarify
    if not hits or (
        hits[0]["similarity"] < SIMILARITY_THRESHOLD and not has_domain_entity(query)
    ):
        return {
            "answer": CLARIFY_REPLY,
            "sources": [],
            "category": category,
            "refused": False,
            "clarify": True,
        }

    messages = _build_messages(query, hits, history=recent)
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,
    )
    answer_text = resp.choices[0].message.content or ""
    return {
        "answer": answer_text,
        "sources": [{"text": h["text"], "source": h["source"]} for h in hits],
        "category": category,
        "refused": False,
        "clarify": False,
    }
