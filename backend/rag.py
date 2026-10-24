"""核心 RAG 链路：文档加载、分块、建库、检索、生成回答。

只实现单一 RAG 链路，不做意图分类 / 安全拒答 / 追问 / 前端 / 评估。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

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
    UPLOADS_DIR_NAME,
)

# 动态上传文档的 category（检索时始终与主领域一起参与）
UPLOAD_CATEGORY = UPLOADS_DIR_NAME

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

# System Prompt：身份、回答深度、引用规则与表达风格。「去机器化」的关键约束放在这里，
# 以独立 system 消息下发的约束力远强于混在 user 内容里
SYSTEM_PROMPT = """你是一位经验丰富、耐心且专业的健身教练，通过问答助手为学员解答训练与营养问题。

回答的深度与温度（重要）：
- 不要惜字如金：不只给结论，还要解释**为什么**，让学员知其然更知其所以然；
- 适用时给出具体的**例子**或**可执行的步骤**，尽量分点列出，逻辑清晰；
- 需要展开的内容就完整展开（300-500 字完全可以），不要生硬压缩，也不要凑字数；
- 回答结尾自然地反问一句（如"你目前的训练频率是多少？""想主要针对哪个部位？"），
  引导学员补充细节、继续对话，而不是生硬结束；
- 学员的问题比较宽泛时，先基于文档与通用经验给出一套系统的建议，
  再在结尾反问引导补充，不要反过来先追问而不作答。

依据与引用规则（必须严格遵守）：
- 以【文档：《名称》】标出的文档内容为主要依据；文档未覆盖的部分，
  可以补充通用的、无争议的健身常识，但不要编造具体数据、研究或医疗建议；
- 绝对禁止出现"片段1""片段2""片段3"或"依据片段…"这类字眼，任何情况下都不允许；
- 引用资料时必须使用文档的中文全称（如"根据《NSCA 卧推技术详解》的说明，……"），
  绝不要使用英文文件名或自造缩写；
- 只能引用列出的文档名，不要编造或改写文档名；
- 仅当所有文档内容都与问题明显无关时，才直说资料覆盖不到；只要有部分相关，
  就基于相关部分展开作答。

表达风格：
- 像真人教练面对学员一样说话：亲切、专业、有温度，直接称呼"你"；
- 口语自然，可以分点但不要机械罗列；不要输出"参考来源 1/2/3"式的列表。"""

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
    _reset_bm25()  # 全量重建后 id 集合已变，BM25 缓存失效待重建
    return {"added": len(ids), "chunks": len(texts), "categories": cat_count}


def add_document(md_path: Path, category: str = UPLOAD_CATEGORY) -> dict:
    """动态增量入库：读取单个文档，切块、向量化后写入 ChromaDB（不重建全库）。

    同名文档重复上传时先删旧块再写入（替换语义），避免产生重复向量。
    返回 {"added": 块数, "source": 入库的 source 路径}。
    """
    source = str(md_path.relative_to(DOCS_DIR.parent))
    content = md_path.read_text(encoding="utf-8")
    chunks = [c.strip() for c in _chunk_text(content)]
    chunks = [c for c in chunks if c and c not in _MD_MARKERS]
    if not chunks:
        return {"added": 0, "source": source}

    collection = _get_collection()
    try:
        collection.delete(where={"source": source})
    except Exception:
        pass  # 首次上传时无旧块可删
    ids = [f"{category}:{md_path.stem}:{idx}" for idx in range(len(chunks))]
    metadatas = [{"source": source, "category": category}] * len(chunks)
    embeddings = _embed(chunks)
    collection.add(ids=ids, embeddings=embeddings, documents=chunks, metadatas=metadatas)
    _reset_bm25()  # 新文档入库后 BM25 缓存失效，下次检索自动带上新内容
    return {"added": len(ids), "source": source}


def list_sources() -> list[str]:
    """返回库中所有不重复的文档来源名（source），按字典序排序。"""
    collection = _get_collection()
    got = collection.get(include=["metadatas"])
    return sorted({m.get("source", "") for m in got["metadatas"]} - {""})


def _search_categories(category: str) -> list[str] | None:
    """根据意图类别确定检索目录：主领域 + 动态上传库。

    上传的通用文档不属于三大领域，但应始终参与检索，
    否则动态添加的知识永远无法被引用。
    """
    if category in ("technique", "nutrition", "injury-prevention"):
        return [category, UPLOAD_CATEGORY]
    return None


# ---- 混合检索（Hybrid Search：向量 + BM25 + RRF）----
HYBRID_FETCH = 10  # 两路通道各取的候选数
RRF_K = 60         # Reciprocal Rank Fusion 的平滑常数：score = Σ 1/(k + rank)

# 内存 BM25 索引缓存：{"index", "chunks", "id2chunk"}；None 表示未构建或不可用
_BM25_STATE: dict | None = None


def _reset_bm25() -> None:
    """文档增删/重建索引后使 BM25 缓存失效，下次检索时自动重建。"""
    global _BM25_STATE
    _BM25_STATE = None


def _get_bm25() -> dict:
    """惰性构建内存 BM25 索引：分块与 ChromaDB 的 id 一一对应。

    jieba / rank_bm25 延迟导入，依赖缺失或构建失败时向上抛异常，
    由 retrieve() 降级为纯向量检索，绝不影响主链路。
    文档名（如《NSCA 深蹲技术详解》）是人工编写的高密度关键词，一并参与索引，
    让"NSCA/ISSN/ACSM"等机构缩写能被关键词通道精确命中。
    """
    global _BM25_STATE
    if _BM25_STATE is not None:
        return _BM25_STATE
    import jieba
    import rank_bm25

    collection = _get_collection()
    got = collection.get(include=["documents", "metadatas"])
    corpus = []
    chunks = []
    for cid, text, meta in zip(got["ids"], got["documents"], got["metadatas"]):
        doc_name = Path(meta.get("source", "")).stem
        corpus.append(list(jieba.cut_for_search(text + " " + doc_name)))
        chunks.append(
            {
                "id": cid,
                "text": text,
                "source": meta.get("source", ""),
                "category": meta.get("category", ""),
            }
        )
    index = rank_bm25.BM25Okapi(corpus)
    _BM25_STATE = {"index": index, "chunks": chunks, "id2chunk": {c["id"]: c for c in chunks}}
    return _BM25_STATE


def retrieve(query: str, top_k: int = TOP_K, categories: list[str] | None = None) -> list[dict]:
    """混合检索：向量 Top10 + BM25 Top10 → RRF 融合取 Top-K。

    返回 [{text, source, category, distance, similarity, rrf, vec_rank, bm25_rank}]。
    - categories 同时约束两路通道；
    - BM25 构建/检索任何异常都自动降级为纯向量检索；
    - 仅 BM25 命中（向量未进前列）的块 similarity 记 0.5，
      避免因缺少向量分被相似度阈值误判为低质。
    """
    q = query.strip()
    if not q:
        return []
    collection = _get_collection()
    q_emb = _embed([q])[0]
    where = {"category": {"$in": list(categories)}} if categories else None
    fetch = max(HYBRID_FETCH, top_k)

    # ---- A) 向量通道：ChromaDB 取 Top fetch（query 结果默认携带 ids）----
    result = collection.query(
        query_embeddings=[q_emb],
        n_results=fetch,
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    vec_hits: dict[str, dict] = {}
    vec_order: list[str] = []
    for cid, text, meta, dist in zip(
        result["ids"][0], result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        d = float(dist)
        vec_order.append(cid)
        vec_hits[cid] = {
            "text": text,
            "source": meta.get("source", ""),
            "category": meta.get("category", ""),
            "distance": d,
            "similarity": 1.0 - d,
        }

    # ---- B) 关键词通道：BM25 取 Top fetch（异常降级 → bm_order 为空）----
    bm_order: list[str] = []
    bm: dict | None = None
    try:
        bm = _get_bm25()
        import jieba

        tokens = list(jieba.cut_for_search(q))
        scores = bm["index"].get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        allowed = set(categories) if categories else None
        for i in order:
            if len(bm_order) >= fetch:
                break
            if scores[i] <= 0:
                continue
            chunk = bm["chunks"][i]
            if allowed is not None and chunk["category"] not in allowed:
                continue
            bm_order.append(chunk["id"])
    except Exception:
        bm = None

    # ---- 降级：BM25 不可用时直接返回纯向量结果 ----
    if not bm_order:
        hits = [dict(vec_hits[cid]) for cid in vec_order[:top_k]]
        for h in hits:
            h.setdefault("rrf", None)
            h.setdefault("vec_rank", None)
            h.setdefault("bm25_rank", None)
        return hits

    # ---- C) RRF 融合：score = Σ 1/(k + rank)，按 id 合并两路排名 ----
    fused: dict[str, float] = {}
    for rank_list in (vec_order, bm_order):
        for rank, cid in enumerate(rank_list, start=1):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
    top_ids = sorted(fused, key=fused.get, reverse=True)[:top_k]

    hits = []
    for cid in top_ids:
        if cid in vec_hits:
            hit = dict(vec_hits[cid])
        else:
            chunk = bm["id2chunk"][cid]
            hit = {
                "text": chunk["text"],
                "source": chunk["source"],
                "category": chunk["category"],
                "distance": 0.5,
                "similarity": 0.5,
            }
        hit["rrf"] = round(fused[cid], 5)
        hit["vec_rank"] = vec_order.index(cid) + 1 if cid in vec_order else None
        hit["bm25_rank"] = bm_order.index(cid) + 1 if cid in bm_order else None
        hits.append(hit)
    return hits


def _doc_name(source: str) -> str:
    """从 source 提取文档名（去路径、去 .md 后缀）。如 docs/technique/protein-intake.md → protein-intake。"""
    return Path(source).stem if source else ""


def rewrite_query(query: str, history: list[dict] | None) -> str:
    """多轮 Query Rewrite：结合历史把追问重写为独立完整的检索句。

    例如历史"深蹲怎么做"+ 当前"对膝盖好吗" → "深蹲对膝盖好吗"，
    解决追问缺乏主语导致检索命中率低的问题。
    任何异常（调用失败 / 输出为空 / 长度异常）都降级返回用户原问题。
    """
    if not history:
        return query
    lines = ["你是一个查询重写助手。请结合历史对话，把用户的当前问题重写为一个不依赖上下文、独立完整的检索句子。只输出重写后的句子，不要任何解释。"]
    lines.append("例如：历史“深蹲怎么做”，当前“对膝盖好吗”，重写为“深蹲对膝盖好吗”。")
    lines.append("历史对话：")
    for h in history[-4:]:
        role = "用户" if h.get("role") == "user" else "助手"
        lines.append(f"{role}：{(h.get('content') or '')[:150]}")
    lines.append(f"当前问题：{query}")
    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": "\n".join(lines)}],
            temperature=0.0,
            # deepseek-flash 是推理模型：思考也要消耗预算，给足（max_tokens=8 的教训）
            max_tokens=512,
        )
        out = (resp.choices[0].message.content or "").strip()
        # 输出消毒：去包裹引号/换行；长度异常视为失败，降级原问题
        out = out.strip("“”‘’\"'「」").replace("\n", " ").strip()
        if out and 2 <= len(out) <= 120:
            return out
    except Exception:
        pass
    return query


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
            有历史时先做 Query Rewrite：把追问重写为独立完整的检索句，
            分类与检索均基于重写句（重写失败降级原问题）；回答生成仍面向原问题。
    """
    recent = history[-4:] if history else None
    search_query = rewrite_query(query, recent)
    category = classify(search_query, history=recent)

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

    # 3) 正常类：按类别目录限定检索（主领域 + 动态上传库），用重写后的独立句检索
    categories = _search_categories(category)
    hits = retrieve(search_query, top_k=top_k, categories=categories)

    # 相似度不足 → 追问而非硬答。
    # 仅限「没有领域实体词」的问题：实体明确（蛋白质/深蹲/热身…）的问题
    # 即使相似度偏低也直接检索回答，绝不降级为 clarify
    if not hits or (
        hits[0]["similarity"] < SIMILARITY_THRESHOLD and not has_domain_entity(search_query)
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


def _sse(payload: dict) -> str:
    """把事件字典编码成一条 SSE 消息（data: JSON\\n\\n）。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def answer_stream(
    query: str, top_k: int = TOP_K, history: list[dict] | None = None
) -> Iterator[str]:
    """流式问答入口：yield SSE 事件字符串，与 answer() 共享同一套分类与检索逻辑。

    事件协议：
      data: {"meta": {"category":…, "refused":…, "clarify":…}}   首个事件，前端渲染徽章
      data: {"text": "…"}                                        文本块（0..n 次）
      data: {"sources": [{text, source}, …]}                     收尾事件（仅正常回答）

    refuse / clarify / 相似度不足时不调大模型：meta + 完整话术单块输出，前端表现一致。
    """
    recent = history[-4:] if history else None
    search_query = rewrite_query(query, recent)
    # 实际发生重写时才在 meta 里带上，供前端感知展示
    rewritten = search_query if search_query != query else None
    category = classify(search_query, history=recent)

    # 1) 拒答：meta + 完整话术，不调大模型
    if category == "refuse":
        yield _sse({"meta": {"category": category, "refused": True, "clarify": False, "rewritten": rewritten}})
        yield _sse({"text": REFUSE_REPLY})
        return

    # 2) 分类即模糊：追问，不检索不生成
    if category == "clarify":
        yield _sse({"meta": {"category": category, "refused": False, "clarify": True, "rewritten": rewritten}})
        yield _sse({"text": CLARIFY_REPLY})
        return

    # 3) 正常类：用重写后的独立句检索（实体词豁免相似度阈值，与 answer() 一致）
    categories = _search_categories(category)
    hits = retrieve(search_query, top_k=top_k, categories=categories)
    low_sim = not hits or (
        hits[0]["similarity"] < SIMILARITY_THRESHOLD and not has_domain_entity(search_query)
    )
    if low_sim:
        yield _sse({"meta": {"category": category, "refused": False, "clarify": True, "rewritten": rewritten}})
        yield _sse({"text": CLARIFY_REPLY})
        return

    # 4) 流式生成
    yield _sse({"meta": {"category": category, "refused": False, "clarify": False, "rewritten": rewritten}})
    messages = _build_messages(query, hits, history=recent)
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    resp = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        messages=messages,
        temperature=0.3,
        stream=True,
    )
    for chunk in resp:
        if not chunk.choices:  # 部分块只有 usage/空 choices
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield _sse({"text": delta})
    yield _sse(
        {
            "sources": [
                {
                    "text": h["text"],
                    "source": h["source"],
                    "rrf": h.get("rrf"),
                    "vec_rank": h.get("vec_rank"),
                    "bm25_rank": h.get("bm25_rank"),
                }
                for h in hits
            ]
        }
    )
