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

## 环境变量

需要设置 `DEEPSEEK_API_KEY` 环境变量（DeepSeek API Key），否则后端启动 / 建库后调用问答会报错。示例：

```bash
# Windows PowerShell
$env:DEEPSEEK_API_KEY = "sk-..."
# 或写入 .env 后由你的环境加载（本项目不自行读取 .env 文件）
```

## 本地运行

前置：本机已下载 bge-small-zh-v1.5 模型到 `models/bge-small-zh-v1.5`（模型不入库）。

```bash
# 0) 创建并启用虚拟环境（如未建）
python -m venv venv
.\venv\Scripts\Activate.ps1

# 1) 安装依赖
.\venv\Scripts\python.exe -m pip install -r requirements.txt
# 若未生成 requirements.txt，可安装：fastapi uvicorn chromadb sentence-transformers openai numpy torch

# 2) 设置 API Key（或用 setx 设成用户级环境变量）
$env:DEEPSEEK_API_KEY = "sk-..."

# 3) 建库（首次会加载本地模型、生成向量，写入 data/chroma_db）
.\venv\Scripts\python.exe -m scripts.build_index

# 4) 启动服务
.\venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

验证：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
# {"status":"ok"}

# /ask（注意：PowerShell 5.1 发送中文 body 需显式转 UTF-8 字节，否则后端收到乱码）
$body = [System.Text.Encoding]::UTF8.GetBytes('{"question":"深蹲时膝盖能不能超过脚尖？"}')
Invoke-RestMethod -Uri http://127.0.0.1:8000/ask -Method Post `
  -ContentType "application/json; charset=utf-8" -Body $body
```

## 安全边界与分类

系统对每个问题先做**意图分类**，再决定走哪条路径：

| 类别 | 行为 |
|---|---|
| `technique` | 只检索 `docs/technique/`，正常问答 |
| `nutrition` | 只检索 `docs/nutrition/`，正常问答 |
| `injury-prevention` | 只检索 `docs/injury-prevention/`，正常问答 |
| `refuse` | **不检索**，返回固定拒答话术（`refused=true`），不生成训练/饮食建议 |
| `clarify` | **不检索**，返回澄清追问（`clarify=true`） |

关键边界逻辑（见 `backend/rag.py` / `backend/classifier.py`）：

- **安全拒答**：命中疑似伤病、用药、疾病饮食、特殊人群关键词（疼/痛/伤/药/病/孕/术后等）的问题，直接 `refused=true` 并引导咨询医生或康复师，**不进入检索**。
- **追问澄清**：问题信息不足（过短、"我该怎么练"之类泛问）或 LLM 分类置信度低时，返回 `clarify=true` 追问，不硬答。
- **按类限定检索**：正常类问题只在其对应类别的目录内检索，避免跨类噪声。
- **相似度阈值 0.5**：检索结果最高相似度低于 0.5 时，视为信息不足而追问，而不是基于不相关内容硬答。

/ask 返回结构：`{ answer, sources, category, refused, clarify }`。
