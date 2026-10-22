# 健身问答助手 Fitness QA Agent

面向健身入门者的 RAG 文档问答助手，回答动作技术、营养原则与伤病预防问题，并对伤病诊疗、用药、疾病饮食等明确拒答引导就医。

详细需求见 [docs/PRD.md](docs/PRD.md)。

## 目录结构

```
fitness-qa-agent/
├── docs/                    # 知识文档库与需求
│   ├── PRD.md               # 需求说明
│   ├── technique/           # 动作技术（深蹲/硬拉/卧推/划船/肩推）
│   ├── nutrition/           # 营养原则（蛋白质/训练前后进食/减脂）
│   └── injury-prevention/   # 伤病预防（热身/恢复/过度使用预防）
├── backend/                 # FastAPI 后端（代码待实现）
├── frontend/                # 简单前端（代码待实现）
├── scripts/                 # 入库脚本、评估脚本（代码待实现）
├── data/                    # 运行期数据（chroma_db、上传文档等）
├── evalset.json             # 评估集（28 条）
├── README.md
└── .gitignore
```

## 运行评估脚本（占位说明）

按 PRD 第 8 节，评估脚本将读取 `evalset.json`，按类别输出指标表（检索命中率、答案覆盖率、token 消耗、安全拒答率、误拒率）。

```bash
# 待实现：运行评估脚本
# python -m scripts.evaluate --evalset evalset.json
```

> 脚本实现见第三步，当前为占位说明。
