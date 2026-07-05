# AgroMech RAG

> 面向农机领域的多模态检索增强生成（RAG）系统。把说明书、维修手册、故障码表、保养规程、配件目录、图纸和扫描件整理成**可检索、可引用、可追溯**的知识库，回答基于来源证据，证据不足时明确说明不确定性。

[![CI](https://github.com/Sreehc/AgroMech/actions/workflows/ci.yml/badge.svg)](https://github.com/Sreehc/AgroMech/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-Agentic%20RAG-FF6F00)

AgroMech 不是通用聊天机器人，而是**农机资料证据助手**：维修、安全、配件和故障判断都必须受证据约束，涉及高风险主题时强制附带安全提醒。

<!-- 截图占位：建议放一张 Assistant 问答工作台截图（带引用面板与安全提醒），以及一张资料库页面截图。
     推荐路径 docs/assets/，例如：
     ![问答工作台](docs/assets/workbench.png)
-->

---

## ✨ 核心能力

- 📥 **多格式资料导入** — PDF、Word、Excel/CSV、TXT/Markdown 和常见图片，后台异步解析。
- 🧩 **多模态解析** — 文本、表格、扫描件 OCR、图片视觉观察，并用 LLM 回填品牌/型号/类型等元数据。
- 🔍 **混合检索** — 关键词、结构化过滤、向量、Vision RAG 多路并行召回，去重合并后 rerank。
- 🤖 **受控多 Agent 问答** — LangGraph 编排解析、路由、检索、证据审查、领域回答、安全审查，流程固定、每步可追溯。
- 🖼️ **图像辅助查询** — 支持仪表盘照片、故障灯、部件照片、液压图、电路图作为检索线索。
- 📎 **带引用的回答** — 每条结论回连到具体文档 chunk 和来源定位，支持证据预览。
- 🛡️ **可信边界** — 证据不足返回高不确定性，拒绝编造维修步骤，高风险主题保留安全提醒。

## 🔒 可信与安全设计

AgroMech 的核心约束是**可信和可追溯**，这也是它区别于通用问答的地方：

- 回答不得编造维修步骤、故障原因、保养周期、油液规格、扭矩或配件号。
- 视觉识别结果只能作为检索线索，不能在无文档证据时直接变成确定维修结论。
- 涉及液压、电气、发动机、制动或旋转部件时，必须保留安全提醒。
- 面对“忽略引用/绕过安全规则/编造资料”这类 prompt 注入时拒答。
- API key、token、密码、内部路径和异常栈不会暴露给普通用户。

问答链路在生成前有 `EvidenceReviewerAgent`（证据准入）、生成后有 `SafetyReviewerAgent`（安全审查）双重把关。

## 🏗️ 架构

```text
Frontend (Next.js 静态导出, assistant-ui)
  └─ /backend/*  ──(Nginx 反代)──▶  FastAPI API
                                     ├─ Auth / Documents / QA / Chat / Trace
                                     ├─ 受控多 Agent 问答 (LangGraph)
                                     └─ 混合检索 (keyword / structured / vector / vision + rerank)
                                          │
        Worker (异步导入)  ──▶  Postgres · Zvec 向量库 · 文件存储 · RabbitMQ
                                          │
                                    阿里云百炼 (LLM / embedding / vision / rerank) · PaddleOCR
```

| 层 | 技术栈 |
| --- | --- |
| 后端 | Python · FastAPI · SQLAlchemy Core · Alembic |
| 前端 | Next.js App Router · React · TypeScript · Tailwind CSS · assistant-ui |
| 问答编排 | LangGraph · langchain-core（仅用于 tool 包装） |
| 数据库 | Postgres |
| 向量检索 | Zvec（嵌入式，数据在 `.agromech-data/zvec`） |
| 文件存储 | 阿里云 OSS，本地开发用 local fallback |
| 模型 | 阿里云百炼（LLM / embedding / vision / rerank）· PaddleOCR 云 API |
| 队列 | RabbitMQ（分发唤醒 worker；`ingest_tasks` 为权威状态） |

> Graph RAG / Neo4j 相关代码保留为后续增强，当前主链路不启用。

## 🚀 快速开始

> **前置依赖**：Postgres 和 RabbitMQ 复用同级目录 `../infrastructure`（一个独立的共享基础设施仓库，提供 compose 与环境配置）。本项目不单独新建这两个服务。如果你没有该仓库，可自行准备一个可访问的 Postgres 和 RabbitMQ，并在 `.env` 中配置 `DATABASE_URL` 与 `RABBITMQ_URL`。

**1. 安装 Python 环境**（要求 Python 3.11+）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env   # 按需修改
```

**2. 启动共享依赖**（使用 `../infrastructure` 时）

```bash
cd ../infrastructure
docker compose --env-file env/.env -f compose/docker-compose.core.yml up -d postgres
docker compose --env-file env/.env -f compose/docker-compose.mq.yml up -d rabbitmq
cd ../AgroMech
```

**3. 迁移数据库并创建管理员**

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/create-user.py --username admin --role admin --display-name "Administrator"
```

**4. 启动后端、worker 和前端**

```bash
# 后端 API (http://127.0.0.1:8000)
.venv/bin/python -m uvicorn agromech_api.main:app --app-dir backend --host 0.0.0.0 --port 8000

# 导入 worker（一次性 DB 队列调度；常驻消费见文档）
.venv/bin/python -m agromech_worker.main

# 前端 (http://localhost:3000)
npm install --prefix frontend
npm run dev --prefix frontend
```

**5. 验证**

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/dependencies
```

更完整的运行、上传、问答和 RabbitMQ 常驻消费说明见 [技术设计](docs/tech-design.md) 和 [部署说明](docs/deployment.md)。

## 🧪 测试

```bash
scripts/test-all.sh   # lint + 后端/worker/前端测试 + 集成 + E2E smoke + 前端构建
```

GitHub Actions 在推送到 `main` 和 PR 时执行同一套检查。

## 📚 文档

开发文档位于 [docs/](docs/README.md)，建议阅读顺序：

1. [产品需求](docs/prd.md) — 产品目标、用户、已实现能力和验收口径
2. [技术设计](docs/tech-design.md) — 后端、worker、混合检索、受控多 Agent 问答、部署
3. [API 规格](docs/api-spec.md) — 真实后端接口、请求响应、权限和错误码
4. [数据库设计](docs/database-design.md) — 关系表、状态机和索引结构
5. [UX 规格](docs/ux-spec.md) — 前端页面、交互和角色权限
6. [部署说明](docs/deployment.md) — 静态前端 + Docker 后端/worker + GitHub Actions
7. [项目历史和决策](docs/history.md) — 关键决策、完成记录和后续增强

## 📄 License

当前仓库尚未声明开源许可协议。在补充 `LICENSE` 文件之前，默认保留所有权利。
