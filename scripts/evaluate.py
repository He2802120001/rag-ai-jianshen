"""评估脚本：读取 evalset.json，调用本地 /ask 服务，按类别统计指标。

独立运行：python -m scripts.evaluate
依赖正在运行的后端服务（http://127.0.0.1:8000）。只评估，不修改 backend/ 业务逻辑。
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

BASE_URL = "http://127.0.0.1:8000"
EVAL_PATH = Path(__file__).resolve().parent.parent / "evalset.json"
RESULT_PATH = Path(__file__).resolve().parent.parent / "docs" / "eval-result.md"

CATEGORIES = ["technique", "nutrition", "injury-prevention", "cross", "refuse", "clarify"]
# 请求/响应超时（秒）
TIMEOUT = 30


def ask(question: str) -> dict | None:
    """以 UTF-8 字节发送中文 body；异常返回 None（计为失败）。"""
    url = f"{BASE_URL}/ask"
    body = json.dumps({"question": question}, ensure_ascii=False).encode("utf-8")
    try:
        resp = requests.post(
            url, data=body, headers={"Content-Type": "application/json"}, timeout=TIMEOUT
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _hit_rate(points: list[str], texts: list[str]) -> float:
    """expected_points 中出现在 texts（拼接后）里的比例。"""
    if not points:
        return 1.0  # 无关键点视为无需检查
    blob = "\n".join(texts)
    hit = sum(1 for p in points if p in blob)
    return hit / len(points)


def main() -> None:
    cases = json.loads(EVAL_PATH.read_text(encoding="utf-8"))["cases"]

    # ---- 逐条请求 ----
    per_case: list[dict] = []
    failures: list[str] = []
    for case in cases:
        cid = case["id"]
        action = case.get("action")
        points = case.get("expected_points", [])
        result = ask(case.get("question", ""))
        if result is None:
            failures.append(f"{cid} ({case.get('category')}) 请求失败")
            per_case.append({"id": cid, "category": case.get("category"), "action": action,
                             "ok": False})
            continue
        per_case.append({
            "id": cid,
            "category": case.get("category"),
            "action": action,
            "ok": True,
            "refused": bool(result.get("refused", False)),
            "clarify": bool(result.get("clarify", False)),
            "answer": result.get("answer", ""),
            "sources": [s.get("text", "") for s in result.get("sources", [])],
            "points": points,
        })

    # ---- 指标（总体）----
    answer_cases = [c for c in per_case if c["ok"] and c["action"] == "answer"]
    refuse_cases = [c for c in per_case if c["ok"] and c["action"] == "refuse"]
    clarify_cases = [c for c in per_case if c["ok"] and c["action"] == "clarify"]

    def agg_retrieval_hit(cases: list[dict]) -> float | None:
        if not cases:
            return None
        return sum(_hit_rate(c["points"], c["sources"]) for c in cases) / len(cases)

    def agg_answer_cover(cases: list[dict]) -> float | None:
        if not cases:
            return None
        return sum(_hit_rate(c["points"], [c["answer"]]) for c in cases) / len(cases)

    def ref_denied(cases: list[dict]) -> float | None:  # 应拒答中被拒比例
        if not cases:
            return None
        return sum(1 for c in cases if c["refused"]) / len(cases)

    def clarify_hit(cases: list[dict]) -> float | None:  # 应追问中被追问比例
        if not cases:
            return None
        return sum(1 for c in cases if c["clarify"]) / len(cases)

    def false_refuse(cases: list[dict]) -> float | None:  # 应回答但被误拒比例
        if not cases:
            return None
        return sum(1 for c in cases if c["refused"]) / len(cases)

    # ---- 输出 1：按类别 ----
    lines_console = []
    header = f"{'类别':<18}{'条目':>4}{'失败':>4}{'检索命中':>9}{'答案覆盖':>9}{'误拒率':>9}{'拒答率':>9}{'追问率':>9}"
    print("\n===== 按类别 =====")
    print(header)
    lines_console.append("| 类别 | 条目 | 失败 | 检索命中率 | 答案覆盖率 | 误拒率 | 安全拒答率 | 追问命中率 |")
    lines_console.append("|---|---|---|---|---|---|---|---|")
    for cat in CATEGORIES:
        cat_cases = [c for c in per_case if c["category"] == cat]
        if not cat_cases:
            continue
        total = len(cat_cases)
        failed = sum(1 for c in cat_cases if not c["ok"])
        ok = [c for c in cat_cases if c["ok"]]
        a = [c for c in ok if c["action"] == "answer"]
        rh = agg_retrieval_hit(a)
        ac = agg_answer_cover(a)
        fr = false_refuse(a)
        rr = ref_denied([c for c in ok if c["action"] == "refuse"])
        ch = clarify_hit([c for c in ok if c["action"] == "clarify"])
        fmt = lambda v: "—" if v is None else f"{v:.3f}"
        print(f"{cat:<18}{total:>4}{failed:>4}{fmt(rh):>9}{fmt(ac):>9}{fmt(fr):>9}{fmt(rr):>9}{fmt(ch):>9}")
        lines_console.append(
            f"| {cat} | {total} | {failed} | {fmt(rh)} | {fmt(ac)} | {fmt(fr)} | {fmt(rr)} | {fmt(ch)} |"
        )

    # ---- 输出 2：总体汇总 ----
    total_all = len(per_case)
    total_failed = len(failures)
    overall_rh = agg_retrieval_hit(answer_cases)
    overall_ac = agg_answer_cover(answer_cases)
    overall_rr = ref_denied(refuse_cases)      # 安全拒答率
    overall_fr = false_refuse(answer_cases)    # 误拒率
    overall_ch = clarify_hit(clarify_cases)    # 追问命中率

    fmt = lambda v: "—" if v is None else f"{v:.3f}"
    print("\n===== 总体汇总 =====")
    print(f"总条目: {total_all}    失败: {total_failed}")
    print(f"检索命中率: {fmt(overall_rh)}")
    print(f"答案覆盖率: {fmt(overall_ac)}")
    print(f"安全拒答率: {fmt(overall_rr)}")
    print(f"误拒率:     {fmt(overall_fr)}")
    print(f"追问命中率: {fmt(overall_ch)}")
    print(f"平均 token: 无（/ask 未返回 token 字段，跳过）")

    if failures:
        print("\n===== 失败条目 =====")
        for f in failures:
            print(f"  - {f}")

    # ---- 写入 docs/eval-result.md ----
    md = [
        "# 评估结果",
        "",
        f"运行时间：按 `/ask` 实际执行（未记录 token，见下）。",
        f"总条目 {total_all}，失败 {total_failed}。《平均 token》：**/ask 未返回 token 字段，无法统计，已跳过**。",
        "",
        "## 按类别",
        "",
    ]
    md += lines_console
    md += [
        "",
        "## 总体汇总",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 检索命中率 | {fmt(overall_rh)} |",
        f"| 答案覆盖率 | {fmt(overall_ac)} |",
        f"| 安全拒答率 | {fmt(overall_rr)} |",
        f"| 误拒率 | {fmt(overall_fr)} |",
        f"| 追问命中率 | {fmt(overall_ch)} |",
        f"| 平均 token | 无（/ask 未返回 token 字段） |",
        "",
    ]
    if failures:
        md += ["## 失败条目", ""]
        for f in failures:
            md += [f"- {f}", ""]
    RESULT_PATH.write_text("\n".join(md), encoding="utf-8")
    print(f"\n结果已写入 {RESULT_PATH}")


if __name__ == "__main__":
    main()
