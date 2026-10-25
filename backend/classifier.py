"""意图分类：把用户问题分到 technique / nutrition / injury-prevention / refuse / clarify。

判定原则（多轮误拦截修复版）：
1. 分类只看「当前问题」的文本；多轮历史仅作为 LLM 兜底分类的参考上下文传入。
   旧版把历史拼接文本一起做关键词匹配，而历史回答里难免出现"疼/伤/诊断/康复"
   等字眼，导致"深蹲前怎么热身"这类正常问题被连坐误判为 refuse。
2. refuse 仅限「明确的医疗求助语义」：怎么治 / 吃什么药 / 确诊 / 手术 /
   急性损伤处理 / 疾病或特殊人群（孕妇等）能否训练。
3. 热身、拉伸、预防、动作要领、怎么做、技巧等技术词直接放行到领域类；
   规则判断不出来时宁可放行去检索，也不在常识性动作问题上误拦。
"""

from __future__ import annotations

import re

from openai import OpenAI

from backend.config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from backend.logger import get_logger

logger = get_logger(__name__)

# 五类标签（与 ChromaDB metadata 的 category 对齐）
CATEGORIES = ("technique", "nutrition", "injury-prevention", "refuse", "clarify")

# ---------- refuse：必须显式命中医疗求助语义才拒答 ----------

# 治疗 / 用药 / 手术 / 确诊 / 康复 / 违禁药物等明确医疗短语
_MEDICAL_HELP_KEYWORDS = (
    "怎么治", "怎么治疗", "如何治疗", "吃什么药", "用什么药", "吃药", "用药",
    "药物", "停药", "开药", "药方", "类固醇", "激素", "兴奋剂",
    "做手术", "能不能做手术", "要不要手术", "需要手术", "术后",
    "确诊", "诊断", "我得了", "患有", "就医", "看病", "康复", "严重吗",
)

# 疾病 / 特殊人群词：出现且伴随训练诉求（练/蹲/做/运动…）→ refuse
_DISEASE_KEYWORDS = (
    "腰椎间盘突出", "腰突", "肩袖损伤", "肩袖撕裂", "腰肌劳损", "糖尿病",
    "高血压", "低血压", "血压高", "血压偏高", "血糖高", "血糖偏高", "血脂稠",
    "心脏病", "冠心病", "肾病", "肝病", "痛风", "哮喘", "骨质疏松", "骨折",
    "滑膜炎", "肌腱炎", "关节炎", "疝气", "贫血", "颈椎病",
)
# 疾病口语化/逆序变体兜底（F-01：防止"血压高/血糖偏高/心跳过快"这类词序
# 颠倒或程度词拼接的表述漏过子串匹配）
_DISEASE_PATTERN = re.compile(
    r"(血压|血糖|尿酸|血脂)(偏高|过高|有点高|高|稠)" r"|(心跳|心率)(偏快|过快|很快|快)"
)
_SPECIAL_POP_KEYWORDS = ("孕妇", "怀孕", "孕期", "产后", "哺乳期", "生理期")
_TRAIN_INTENT_PATTERN = re.compile(r"练|蹲|推|拉|做|动|健身|运动|训练|减脂|增肌")

# 急性损伤词 + 处理求助语义 → refuse（如"腰扭了怎么办"）
_ACUTE_INJURY_KEYWORDS = (
    "扭到", "扭伤", "扭了", "崴了", "崴脚", "闪了", "闪到", "拉伤", "撕裂",
    "断了", "脱臼", "摔伤", "肿胀", "淤青",
)
_INJURY_HELP_PATTERN = re.compile(r"怎么办|怎么处理|怎么恢复|多久能|还能不|严不严重")


def _is_refuse(q: str) -> bool:
    """仅当当前问题带有明确医疗求助语义时返回 True。"""
    # 1) 治疗 / 用药 / 手术 / 确诊 / 违禁药物类明确短语
    if any(kw in q for kw in _MEDICAL_HELP_KEYWORDS):
        return True
    # 2) 疾病（含口语逆序变体）或特殊人群 + 训练诉求：
    #    "腰椎间盘突出还能深蹲吗" / "我血压高还能做硬拉吗" / "孕妇能练吗"
    has_disease = any(kw in q for kw in _DISEASE_KEYWORDS) or bool(_DISEASE_PATTERN.search(q))
    if has_disease and _TRAIN_INTENT_PATTERN.search(q):
        return True
    if any(kw in q for kw in _SPECIAL_POP_KEYWORDS) and _TRAIN_INTENT_PATTERN.search(q):
        return True
    # 3) 急性损伤 + 处理求助："腰扭了怎么办"
    if any(kw in q for kw in _ACUTE_INJURY_KEYWORDS) and _INJURY_HELP_PATTERN.search(q):
        return True
    return False


# ---------- 领域分类：technique / nutrition / injury-prevention ----------

# 伤病预防类词（命中按双倍计分：热身/恢复类问题优先归预防，检索才能命中对应文档）
_INJURY_PREVENTION_KEYWORDS = (
    "热身", "拉伸", "预防", "受伤", "恢复", "放松", "训练量", "疲劳",
    "劳损", "睡眠", "过度使用", "过度训练",
)
_TECHNIQUE_KEYWORDS = (
    "深蹲", "硬拉", "卧推", "划船", "肩推", "推举", "引体", "动作", "姿势",
    "要领", "技巧", "握距", "站距", "站姿", "轨迹", "弓背", "膝盖", "脚尖",
    "手腕", "手肘", "杠铃", "哑铃", "怎么做", "怎么练", "发力",
)
_NUTRITION_KEYWORDS = (
    "吃", "蛋白", "碳水", "脂肪", "热量", "饮食", "减脂", "增肌", "营养",
    "膳食", "维生素", "水分", "补剂", "蛋白粉", "餐", "能量", "缺口",
)

# 泛问 / 信息不足 → clarify（仅当没有任何领域信号时才落到这里）
_CLARIFY_KEYWORDS = ("怎么安排", "该吃什么", "应该怎么", "怎么吃", "帮我")

# 宽泛但有明确训练/营养意图：怎么才能练得更好 / 如何提升训练效果 / 怎么吃得更健康
# 这类问题不 clarify，直接放行到领域类检索通用原则文档作答，结尾再引导补充细节
_BROAD_INTENT_PATTERN = re.compile(
    r"(怎么|如何|怎样|怎么才|怎样才|怎么能).{0,8}"
    r"(练|训|更好|提升|进步|变强|效果|水平|吃|增肌|减脂|身材|肌肉|力量)"
)

_MIN_QUESTION_LEN = 5


def _domain_category(q: str) -> str | None:
    """按领域关键词计分返回三类领域之一；无任何命中返回 None。

    预防类词双倍计分，保证"深蹲前怎么热身"（深蹲=技术、热身=预防 1:1）
    这类混合问题归入 injury-prevention，检索才能命中热身文档。
    """
    tech = sum(1 for kw in _TECHNIQUE_KEYWORDS if kw in q)
    nut = sum(1 for kw in _NUTRITION_KEYWORDS if kw in q)
    inj = 2 * sum(1 for kw in _INJURY_PREVENTION_KEYWORDS if kw in q)
    best = max(
        ("technique", tech), ("nutrition", nut), ("injury-prevention", inj),
        key=lambda x: x[1],
    )
    return best[0] if best[1] > 0 else None


def has_domain_entity(question: str) -> bool:
    """问题是否包含明确的领域实体词或宽泛训练/营养意图（动作技术 / 营养 / 伤病预防任一命中）。

    用于 rag 的相似度兜底：有领域信号的问题必须直接检索回答，
    绝不因检索相似度低而降级为 clarify。
    """
    q = (question or "").strip()
    if _BROAD_INTENT_PATTERN.search(q):
        return True
    return any(
        kw in q
        for kw in _TECHNIQUE_KEYWORDS + _NUTRITION_KEYWORDS + _INJURY_PREVENTION_KEYWORDS
    )


def _llm_classify(question: str, history: list[dict] | None = None) -> str:
    """用 DeepSeek 做一次轻量分类，返回 CATEGORIES 之一；异常时返回空串。"""
    prompt = (
        "你是健身助手的意图分类器。结合历史对话（如有），把学员的当前问题分类到以下五类之一：\n"
        "- technique：问动作技术/要领/姿势/怎么做某个动作\n"
        "- nutrition：问营养/饮食/蛋白质/减脂增肌摄入\n"
        "- injury-prevention：问热身/拉伸/恢复/伤病预防\n"
        "- refuse：仅当问题明确提出伤病诊断、治疗、用药、手术、康复、急性受伤"
        "（扭伤/拉伤/肿痛）的处理求助，或疾病/特殊人群（孕妇等）能否训练\n"
        "- clarify：完全看不出学员想问什么的空泛问题。"
        "注意：宽泛但有明确训练/营养意图的问题（如“怎么才能练得更好”）应归 "
        "technique/nutrition，而不是 clarify\n"
        "两条铁律（最高优先级，必须遵守）：\n"
        "1. 问题中出现任何领域实体词（蛋白质、碳水、减脂、增肌、深蹲、硬拉、"
        "卧推、热身、拉伸、预防、恢复等）时，必须归入对应领域类，绝不 clarify——"
        "即使问题带有“多少/怎么/如何”这类疑问词。\n"
        "2. 热身、拉伸、预防、恢复属于伤病预防范畴，绝不 refuse；"
        "只有明确医疗求助（诊断/治疗/用药/手术/特殊人群能否训练）才 refuse。\n"
        "只输出一个类别标签，不要其他文字。\n"
    )
    if history:
        prompt += "历史对话（仅供理解当前问题，不要因历史中出现伤病字眼而拒答）：\n"
        for h in history[-2:]:
            role = "学员" if h.get("role") == "user" else "教练"
            prompt += f"{role}：{h.get('content', '')[:80]}\n"
    prompt += f"当前问题：{question}"
    try:
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL, timeout=60)
        resp = client.chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            # deepseek-flash 是推理模型，会先输出思考再给正文；
            # 预算太小（如 8）会被思考耗尽导致正文为空
            max_tokens=1024,
        )
        label = (resp.choices[0].message.content or "").strip().lower()
        for cat in CATEGORIES:
            if cat in label:
                return cat
        logger.warning("发生降级：LLM 分类输出无法解析（%.60r），回退关键词规则", label)
    except Exception as e:
        logger.warning("发生降级：LLM 意图分类失败（超时/调用异常），回退关键词规则: %s", e)
    return ""


def classify(question: str, history: list[dict] | None = None) -> str:
    """返回五类之一的意图标签。history 仅作为 LLM 兜底分类的参考，不参与关键词匹配。"""
    q = (question or "").strip()
    if not q:
        return "clarify"

    # 1) 过短问题 → 信息不足，归 clarify
    if len(q) < _MIN_QUESTION_LEN:
        return "clarify"

    # 2) 防伤询问铁律：「怎么预防 / 如何预防 / 避免 / 防止…」直接归 injury-prevention，
    #    严禁 refuse/clarify。仅当同时出现明确医疗短语、疾病或特殊人群词时，
    #    才交给下方 refuse 判定处理（如"术后怎么预防粘连"）
    if re.search(r"预防|防止|避免", q) and not (
        any(kw in q for kw in _MEDICAL_HELP_KEYWORDS)
        or any(kw in q for kw in _DISEASE_KEYWORDS + _SPECIAL_POP_KEYWORDS)
    ):
        return "injury-prevention"

    # 3) 明确医疗求助语义 → refuse（只看当前问题文本，绝不扫历史）
    if _is_refuse(q):
        return "refuse"

    # 4) 领域关键词：热身/拉伸/预防/动作要领/怎么做/技巧等直接放行到领域类
    domain = _domain_category(q)
    if domain:
        return domain

    # 4.5) 宽泛但有明确训练/营养意图（"怎么才能练得更好"）：放行到领域类作答，
    #      由通用原则文档 + 大模型展开回答，结尾引导补充细节，而非直接追问
    if _BROAD_INTENT_PATTERN.search(q):
        return "nutrition" if any(kw in q for kw in _NUTRITION_KEYWORDS) else "technique"

    # 5) 无任何领域信号的泛问 → clarify
    if any(kw in q for kw in _CLARIFY_KEYWORDS):
        return "clarify"

    # 6) 规则未命中 → LLM 分类；仍失败时归 clarify。
    #    能走到这里的都是无实体词的模糊问题，请用户补充信息比瞎猜领域更合理
    label = _llm_classify(q, history=history)
    if label in CATEGORIES:
        return label
    return "clarify"
