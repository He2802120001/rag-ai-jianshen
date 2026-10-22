"""意图分类：把用户问题分到 technique / nutrition / injury-prevention / refuse / clarify。

策略：先规则匹配（refuse 高优先级、过短/泛问归 clarify），规则未命中再用一次轻量 LLM 分类。
"""

from __future__ import annotations

from openai import OpenAI

from backend.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# 五类标签（与 ChromaDB metadata 的 category 对齐）
CATEGORIES = ("technique", "nutrition", "injury-prevention", "refuse", "clarify")

# 疑似伤病 / 医疗 / 特殊人群 关键词 → refuse（优先于领域关键词）
_REFUSE_KEYWORDS = (
    "疼", "痛", "伤", "扭", "骨折", "发炎", "肿", "药", "手术", "术后",
    "病", "糖尿", "肾病", "肝病", "心脏", "血压", "孕", "怀孕", "康复",
    "治疗", "诊断", "腰椎间盘突出", "肩袖", "损伤", "痛风", "就医",
)

# 模糊 / 信息不足 → clarify
_CLARIFY_KEYWORDS = ("怎么练", "怎么安排", "该吃什么", "应该怎么", "怎么吃", "如何练", "帮我")

# 营养类关键词
_NUTRITION_KEYWORDS = (
    "吃", "蛋白", "碳水", "脂肪", "热量", "饮食", "减脂", "增肌", "营养",
    "膳食", "维生素", "水分", "补剂", "蛋白粉", "餐", "能量", "缺口",
)

# 伤病预防类关键词
_INJURY_KEYWORDS = (
    "热身", "恢复", "睡眠", "训练量", "预防", "过度使用", "拉伸", "疲劳",
    "劳损", "放松",
)

# 动作技术类关键词
_TECHNIQUE_KEYWORDS = (
    "深蹲", "硬拉", "卧推", "划船", "肩推", "推举", "动作", "姿势", "姿势",
    "要领", "错误", "握距", "站姿", "轨迹", "弓背", "膝盖", "脚尖",
)

_MIN_QUESTION_LEN = 5


def _llm_classify(question: str) -> str:
    """用 DeepSeek 做一次轻量分类，返回 CATEGORIES 之一；异常时兜底返回 clarify。"""
    prompt = (
        "你是健身助手的意图分类器。把用户问题分类到以下五类之一：\n"
        "- technique：问动作技术/要领/姿势\n"
        "- nutrition：问营养/饮食/蛋白质/减脂增肌摄入\n"
        "- injury-prevention：问热身/恢复/伤病预防\n"
        "- refuse：涉及具体伤病诊断、治疗、康复、用药、疾病饮食或特殊人群\n"
        "- clarify：问题信息不足，无法确定具体想问什么\n"
        f"只输出一个类别标签，不要其他文字。\n问题：{question}"
    )
    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=8,
        )
        label = (resp.choices[0].message.content or "").strip().lower()
        for cat in CATEGORIES:
            if cat in label:
                return cat
    except Exception:
        # LLM 调用失败时保守兜底为 clarify，避免误回答
        pass
    return "clarify"


def classify(question: str) -> str:
    """返回五类之一的意图标签。"""
    q = (question or "").strip()
    if not q:
        return "clarify"

    # 1) 过短问题 → 信息不足，归 clarify
    if len(q) < _MIN_QUESTION_LEN:
        return "clarify"

    # 2) refuse 关键词（安全边界，优先）
    for kw in _REFUSE_KEYWORDS:
        if kw in q:
            return "refuse"

    # 3) 明确的泛问 → clarify
    for kw in _CLARIFY_KEYWORDS:
        if kw in q and not any(k in q for k in _TECHNIQUE_KEYWORDS):
            # 如 "我该怎么练深蹲" 含动作词不该归泛问，交给后续判断
            return "clarify"

    # 4) 领域关键词：动作 > 营养 > 预防（按出现频次/顺序判断）
    tech_hits = sum(1 for kw in _TECHNIQUE_KEYWORDS if kw in q)
    nut_hits = sum(1 for kw in _NUTRITION_KEYWORDS if kw in q)
    inj_hits = sum(1 for kw in _INJURY_KEYWORDS if kw in q)
    best = max(
        ("technique", tech_hits), ("nutrition", nut_hits), ("injury-prevention", inj_hits),
        key=lambda x: x[1],
    )
    if best[1] > 0:
        return best[0]

    # 5) 规则未命中 → LLM 分类
    return _llm_classify(q)
