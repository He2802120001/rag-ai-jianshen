# 健身问答助手 Fitness QA Agent

面向健身入门者的 RAG 文档问答助手，回答动作技术、营养原则与伤病预防问题，并对伤病诊疗、用药、疾病饮食等明确拒答引导就医。

详细需求见 [docs/PRD.md](docs/PRD.md)，深度质量审计见 [docs/audit-report.md](docs/audit-report.md)。

## ✨ 项目高光 / 技术亮点

### 检索管线：企业级 Hybrid RAG

- **多轮 Query Rewrite**：结合历史把追问重写为独立检索句（"对膝盖有伤害吗" → "深蹲对膝盖有伤害吗"），解决追问缺主语导致召回率暴跌的问题；顺带让**上下文中的医疗求助**（历史"腰扭了"+追问"还能练吗"）也能被正确安全拦截。
- **混合检索（Hybrid Search）**：bge-small-zh 向量召回 + jieba/RankBM25 关键词召回双通道各取 Top 10，**RRF（Reciprocal Rank Fusion, k=60）** 融合排序；文档名参与 BM25 索引，"NSCA / ISSN / ACSM"等机构缩写可被关键词通道精确命中；BM25 构建失败自动降级纯向量，绝不崩溃。
- **反幻觉兜底**：领域实体词豁免相似度阈值（"有实体绝不误追问"）；无文档支持的问题（如"怎么造原子弹"）诚实追问而非编造。
- **流式生成**：SSE 事件协议（`meta → text* → sources`），首个 meta 事件即渲染拦截/追问徽章与 Query Rewrite 提示。

### 安全护栏：多层纵深防御

- **三层意图分类**：预防类铁律放行（热身/拉伸/预防绝不拦截）→ 明确医疗语义拦截（疾病词表 + **口语逆序正则**兜底"血压高/血糖偏高/心跳过快"，用药/类固醇/兴奋剂词表，急性损伤 + 求助语义，特殊人群 + 训练诉求）→ 宽泛训练意图放行（"怎么才能练得更好"）。
- **动态知识库安全**：Admin Token 鉴权、伪科学敏感词过滤（"包治百病/停药/绝对安全"等 11 词）、文件名路径穿越归一化、扩展名白名单、大小上限；增量入库即时参与检索。
- **前端 XSS 白名单渲染**：先全量 HTML 转义，仅还原 `<strong>/<br>`，7 种攻击向量实测零存活注入点。
- **超时治理**：LLM 调用全链路 60s 超时 + 前端 45s 首事件看门狗（与用户主动停止分流提示）。
- **工业级可观测性与安全防御**：使用标准 Logger 实现全链路文件轮转日志与异常降级追踪；引入 SlowAPI 实现基于 IP 的接口限流，防止恶意刷取 Token，并确保静态资源不受限流影响。
- **高内聚低耦合与自动化测试**：落地 21 个分类回归用例，重构核心链路消除重复逻辑，确保流式与非流式接口的安全决策高度一致。

### 产品级体验

- 原生 JS 磨砂玻璃 UI（backdrop-filter + 悬浮卡片）；SSE 流式打字机（▌ 闪烁光标）+ AbortController 停止生成。
- **localStorage 多会话历史**：完整状态恢复（拦截徽章、来源标签、时间戳原样重现）、生成中防切换竞态、流式安全锁。
- 知识库管理后台（Token 登录 / 拖拽上传 / 文档列表 / Toast）与请求审计日志（`[ask]` 全量留痕）。

### 工程深水区踩坑实录（本仓库最宝贵的部分）

1. **推理模型 `max_tokens=8` 静默截断**：deepseek-flash 先输出思考再给正文，8 个 token 被思考耗尽 → 分类结果永远为空 → 分类器上线即静默降级。教训：推理模型上任何 token 截断都是**无报错的静默失败**。
2. **多轮历史拼接污染关键词分类**：历史回答里的"疼/伤/诊断"字眼让"深蹲前怎么热身"被连坐误拦。教训：关键词匹配的对象必须是**当前问题**，历史只配做语义参考。
3. **CSS `display:flex` 覆盖 `hidden` 属性**：模态框三路关闭全部失效。教训：给 `[hidden]` 配 `display:none !important` 保险。
4. **hover `transform` 引发滚动条振荡重排**：`overflow-y:auto` 隐式开启横向滚动条，位移 2px 触发"滚动条出现↔消失"无限循环。教训：hover 只改颜色/阴影，永不改几何。

### 质量保障

21 用例分类回归单测（全绿）、5 路并发流式压测（零串台）、深度安全审计（XSS 7 向量 / 越狱 / 路径穿越 / 鉴权 / 反幻觉 / 错别字容错），审计结论与修复记录见 [docs/audit-report.md](docs/audit-report.md)。

**技术栈**：FastAPI · ChromaDB · Sentence-Transformers (bge-small-zh-v1.5) · DeepSeek (OpenAI SDK) · jieba + RankBM25 · 原生 HTML/CSS/JS（零前端框架）

## 目录结构

```
fitness-qa-agent/
├── docs/                    # 知识文档库与需求
│   ├── PRD.md               # 需求说明
│   ├── audit-report.md      # 深度质量审计报告
│   ├── technique/           # 动作技术（《NSCA 深蹲/硬拉/卧推/划船/肩上推举技术详解》）
│   ├── nutrition/           # 营养原则（《ISSN 运动营养指南》《中国居民膳食指南_减脂饮食原则》）
│   ├── injury-prevention/   # 伤病预防（《ACSM 热身/训练量管理/恢复与睡眠》）
│   └── uploads/             # 运行期动态上传文档（经管理后台鉴权入库）
├── backend/                 # FastAPI 后端（main / rag / classifier / config）
├── frontend/                # 原生前端（index.html / app.js / style.css）
├── scripts/                 # 建库脚本 build_index、评估脚本 evaluate
├── data/                    # 运行期数据（chroma_db）
├── evalset.json             # 评估集（28 条）
├── requirements.txt
└── README.md
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

## Docker 部署（本地一键测试 / 阿里云 2核2G）

镜像只含代码与依赖（python:3.11-slim + CPU 版 torch）；`models/`、`data/`、`docs/uploads/` 与 `.env` 全部不进镜像，由 docker-compose 在运行时挂载/注入，重建容器不丢数据、不泄露密钥。

```bash
# 0) 首次准备：复制 .env.example 为 .env，填入真实 DEEPSEEK_API_KEY
#    （models/ 与 data/ 已就位即可，无需改动）

# 1) 一键构建并后台启动
docker compose up -d --build

# 2) 查看健康状态与日志（健康检查每 30s curl 一次 /health）
docker compose ps
docker compose logs -f

# 3) 验证
curl http://localhost:8000/health   # {"status":"ok"}

# 停止 / 彻底移除容器（挂载的 models、data、docs/uploads 不受影响）
docker compose down
```

服务器部署要点（阿里云 2核2G）：

- 把项目目录（含 `models/`、`data/`、`.env`）同步到服务器后同样 `docker compose up -d --build`；镜像约 2~3GB，`pip install` 需拉取 CPU 版 torch，首次构建预留 10~20 分钟。
- `restart: always` + `mem_limit: 1500m`：进程崩溃或超内存被杀都会自动拉起；日志轮转 10MB×3 防写满磁盘。
- 记得在阿里云安全组放行 8000 端口（或仅放行 nginx 反代所在端口）。
- 建议把 `ADMIN_TOKEN` 写进服务器上的 `.env` 换成强随机串，不要用默认值。

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

- **防伤铁律优先放行**：包含"预防/防止/避免"且无明确医疗语境的问题直接归 `injury-prevention`，严禁误拦（"怎么预防肩膀受伤"）。
- **明确医疗语义才拒答**：治疗/用药/手术/确诊短语、疾病或特殊人群 + 训练诉求（含"血压高"等口语逆序正则兜底）、急性损伤 + 求助语义；分类只看当前问题文本，多轮历史仅作 LLM 兜底参考（避免历史文本连坐误判）。
- **宽泛意图放行**：有明确训练/营养意图但无具体动作的问题（"怎么才能练得更好"）直接检索通用原则作答，结尾引导补充细节，而非先追问。
- **按类限定检索 + 混合召回**：主领域 + 动态上传库同时参与；向量 + BM25 双通道 RRF 融合。
- **实体词豁免相似度阈值**：含领域实体词的问题即使检索相似度低于 0.5 也直接作答；无实体且低相似度才追问，杜绝编造。

/ask 返回结构：`{ answer, sources, category, refused, clarify }`。

## 评估结果

评估在本机后端对 `evalset.json` 的 28 条逐一执行 `/ask` 得到。完整明细见 [docs/eval-result.md](docs/eval-result.md)。

### 按类别

| 类别 | 条目 | 失败 | 检索命中率 | 答案覆盖率 | 误拒率 | 安全拒答率 | 追问命中率 |
|---|---|---|---|---|---|---|---|
| technique | 7 | 0 | 0.667 | 0.476 | 0.000 | — | — |
| nutrition | 5 | 0 | 0.500 | 0.233 | 0.000 | — | — |
| injury-prevention | 4 | 0 | 0.792 | 0.500 | 0.000 | — | — |
| cross | 4 | 0 | 0.083 | 0.167 | 0.000 | — | — |
| refuse | 5 | 0 | — | — | — | 1.000 | — |
| clarify | 3 | 0 | — | — | — | — | 1.000 |

### 总体汇总

| 指标 | 数值 |
|---|---|
| 检索命中率 | 0.533 |
| 答案覆盖率 | 0.358 |
| 安全拒答率 | 1.000 |
| 误拒率 | 0.000 |
| 追问命中率 | 1.000 |
| 平均 token | 无（/ask 未返回 token 字段） |

### 说明

- `cross` 类检索命中率偏低，是因为当前**跨类检索未实现**——cross 问题需要跨多个文档目录检索，属已知范围限制，待后续切片补齐。
- 安全拒答率 100%，追问命中率 100%，说明伤病/医疗拒答与模糊追问两类边界机制符合预期。
- 修复意图分层误拒后：误拒率从 **0.1 降至 0**，安全拒答率保持 1.000，检索命中率从 **0.467 提升至 0.533**。
