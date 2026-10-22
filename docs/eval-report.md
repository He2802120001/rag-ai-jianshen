# 评估集校验报告

> 记录 `evalset.json`（28 条）中各用例 `expected_points` 与知识库文档的比照结果，作为评估集依据的存档。
> 依据文档：`docs/technique/`、`docs/nutrition/`、`docs/injury-prevention/`，以及 PRD 安全边界（4.1–4.3 节）。

## 类别分布

**technique 7 / nutrition 5 / injury-prevention 4 / cross 4 / refuse 5 / clarify 3**（合计 28 条）

## 用例比照

| ID | 类别 | 依据来源（文档可查） | 有据? |
|---|---|---|---|
| mvp_001 | technique | squat.md——膝盖过脚尖（文档未直接写出"可过脚尖"，属动作要领延伸） | ⚠️ 见 note A |
| mvp_002 | technique | deadlift.md——圆背起拉/腰椎 | ✅ |
| mvp_003 | technique | squat.md——膝盖内扣/弹力带/足弓塌陷 | ✅ |
| mvp_004 | technique | bench-press.md——手腕过度后伸/中立 | ✅ |
| mvp_005 | technique | row.md——坐姿绳索划船/减少腰部负荷 | ✅ |
| mvp_006 | technique | overhead-press.md——头顶正上方/垂直上行 | ✅ |
| mvp_007 | technique | deadlift.md——六角杠硬拉/减少腰部力矩 | ✅ |
| nut_001 | nutrition | protein-intake.md——1.4-2.0 g/kg、1.62 平台 | ✅ |
| nut_002 | nutrition | pre-post-workout-meal.md——训练前1-2h/碳水蛋白/避免过饱 | ✅ |
| nut_003 | nutrition | fat-loss-diet.md——300-500千卡/可持续 | ✅ |
| nut_004 | nutrition | protein-intake.md——确认过敏/不提供处方 | ✅ |
| nut_005 | nutrition | pre-post-workout-meal.md——24h/蛋白碳水一餐 | ✅ |
| inj_001 | injury-prevention | warm-up.md——5-10分/低强度/微汗 | ✅ |
| inj_002 | injury-prevention | recovery.md——7-9h/补水补蛋白 | ✅ |
| inj_003 | injury-prevention | overuse-prevention.md——10%/循序渐进/早期信号 | ✅ |
| inj_004 | injury-prevention | warm-up.md——静态拉伸价值被质疑/不当热身 | ✅ |
| cross_001 | cross | pre-post + recovery——蛋白碳水一餐/7-9h睡眠 | ✅ |
| cross_002 | cross | warm-up.md——一般热身/动态拉伸/空杆专项 | ✅ |
| cross_003 | cross | deadlift + overuse——脊柱中立/贴身上行/循序渐进 | ✅ |
| cross_004 | cross | fat-loss + protein——缺口/高蛋白/保留瘦体重 | ✅ |
| refuse_001 | refuse | PRD 安全边界（非文档） | ✅(边界) |
| refuse_002 | refuse | PRD 安全边界 | ✅(边界) |
| refuse_003 | refuse | PRD 安全边界 | ✅(边界) |
| refuse_004 | refuse | PRD 安全边界 | ✅(边界) |
| refuse_005 | refuse | PRD 安全边界 | ✅(边界) |
| clarify_001 | clarify | PRD 4.3 模糊问题（非文档） | ✅(边界) |
| clarify_002 | clarify | PRD 4.3 | ✅(边界) |
| clarify_003 | clarify | PRD 4.3 | ✅(边界) |

## Note A

`mvp_001` 的 expected_points（"膝关节活动不受脚尖限制/在自然活动范围内即可/因人而异"）**在 squat.md 中没有直接原文依据**——当时文档只写"下蹲深度以大腿接近平行、腰背保持中立、脚跟不离地为宜"，未正面回答"膝盖是否可过脚尖"。这是项目设计的"脏数据"区（PRD 说明矛盾处保留作为检索素材），但如需精确检索命中，需补充关于膝盖过脚尖的表述。

> 已在第二步后续流程补充：见 `docs/technique/squat.md` "动作要领"末尾新增表述——"膝盖在保持足弓和脚跟贴地的前提下可以超过脚尖；是否过脚尖取决于髋踝活动度和动作控制。"
