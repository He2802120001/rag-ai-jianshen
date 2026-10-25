"""意图分类器回归单测（21 用例）：pytest 可直接跑，也可 python tests/test_classifier.py 执行。

覆盖五类判定 + 安全边界铁律：
- technique / nutrition / injury-prevention / refuse / clarify 常规路径
- 疾病口语逆序（血压偏高）、术后+预防的例外、急性损伤求助、特殊人群
- 防伤铁律优先放行、预防词双倍计分、多轮历史不连坐当前问题

注意：全部用例走关键词规则路径，不触发 LLM 兜底（避免测试依赖外网与 API Key）。
"""

import os
import sys
from pathlib import Path

# config 导入期强制要求 DEEPSEEK_API_KEY；单测只测关键词规则，占位即可
os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test-placeholder")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.classifier import classify  # noqa: E402

# (问题, 期望类别, 说明)
CASES = [
    # ---- technique（4）----
    ("深蹲时膝盖能不能超过脚尖？", "technique", "动作技术实体词直接归技术类"),
    ("卧推手腕疼怎么办？", "technique", "手腕疼无急性损伤词+无医疗语义，仍是技术调整问题"),
    ("硬拉的标准动作要领是什么", "technique", "动作+要领关键词"),
    ("怎么才能练得更好", "technique", "宽泛训练意图放行到技术类，不追问"),
    # ---- nutrition（4）----
    ("增肌期每天要吃多少蛋白质", "nutrition", "增肌+蛋白摄入"),
    ("减脂期一天吃多少蛋白质？", "nutrition", "减脂+蛋白摄入"),
    ("练完马上吃东西吗？", "nutrition", "训练后饮食时机"),
    ("怎么吃才能健康减脂", "nutrition", "宽泛营养意图"),
    # ---- injury-prevention（4）----
    ("深蹲前怎么热身？", "injury-prevention", "预防词双倍计分：热身 2 分 > 深蹲 1 分"),
    ("怎么预防肩膀受伤？", "injury-prevention", "防伤铁律：预防询问绝不 refuse"),
    ("训练后怎么放松恢复", "injury-prevention", "放松+恢复预防类词"),
    ("睡眠对训练恢复重要吗", "injury-prevention", "睡眠+恢复"),
    # ---- refuse（5）----
    ("我腰椎间盘突出还能深蹲吗？", "refuse", "疾病 + 训练诉求"),
    ("血压偏高能不能做硬拉", "refuse", "疾病口语逆序正则兜底"),
    ("我腰扭了怎么办？", "refuse", "急性损伤 + 处理求助"),
    ("孕妇能练深蹲吗", "refuse", "特殊人群 + 训练诉求"),
    ("术后怎么预防粘连", "refuse", "预防词遇明确医疗语境（术后）仍拒答"),
    # ---- clarify + 多轮（4）----
    ("在吗", "clarify", "过短问题信息不足"),
    ("帮我看看怎么安排", "clarify", "无任何领域信号的泛问"),
    ("应该怎么开始", "clarify", "宽泛意图不匹配训练/营养实体"),
    # 多轮历史不连坐：历史含伤病内容，当前问题仍按自身文本归类
    ("卧推握距怎么选", "technique", "历史含确诊内容但当前问题是纯技术问题"),
]

HISTORY_CASE = CASES[-1][0]  # 最后一条用例带历史上下文


def _classify_with_history():
    history = [
        {"role": "user", "content": "我确诊了肩袖损伤"},
        {"role": "assistant", "content": "这个问题涉及诊断，建议咨询医生。"},
    ]
    return classify(HISTORY_CASE, history=history)


def _make_test(idx):
    question, expected, _ = CASES[idx]

    def test(self=None):
        got = classify(question)
        assert got == expected, f"[用例{idx + 1}] {question!r}: 期望 {expected}, 实际 {got}"

    test.__doc__ = f"用例{idx + 1}: {question} -> {expected}"
    return test


# 生成前 20 个纯问题用例；第 21 例带历史上下文
for _i in range(len(CASES) - 1):
    setattr(sys.modules[__name__], f"test_case_{_i + 1:02d}", _make_test(_i))


def test_case_21():
    got = _classify_with_history()
    assert got == "technique", f"[用例21] 带伤病历史提问 {HISTORY_CASE!r}: 期望 technique, 实际 {got}"


if __name__ == "__main__":
    passed = 0
    failed = 0
    for i, (question, expected, note) in enumerate(CASES, start=1):
        try:
            got = (
                _classify_with_history()
                if i == len(CASES)
                else classify(question)
            )
            assert got == expected, f"期望 {expected}, 实际 {got}"
            print(f"  PASS {i:02d}  {expected:<18} {question}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL {i:02d}  {question}: {e}")
            failed += 1
    print(f"\n结果: {passed} 通过 / {failed} 失败（共 {len(CASES)} 用例）")
    sys.exit(1 if failed else 0)
