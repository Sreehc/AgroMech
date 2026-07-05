# 项目历史和决策

本文保留对当前项目仍有价值的历史记录、关键决策和后续增强项。旧的临时计划、归档草案和重复设计文档已合并删除。

## 1. 当前基线

截至当前代码状态：

- 后端/worker 测试：`304 passed, 6 warnings`。
- 前端测试：`96 passed`。
- 文档同步测试覆盖当前 docs 文件名、API 关键字段、RabbitMQ worker、Agent Controller。

## 2. 已确认技术决策

- 后端使用 FastAPI + SQLAlchemy Core + Alembic。
- 前端使用 Next.js App Router + React + TypeScript + Tailwind CSS + assistant-ui。
- Postgres、RabbitMQ 复用同级目录 `../infrastructure`；Neo4j / Graph RAG 暂不在当前主链路启用。
- Zvec 作为嵌入式向量库，数据在本项目 `.agromech-data/zvec`。
- 文件存储支持 local fallback 和阿里云 OSS。
- LLM、embedding、vision、rerank 使用阿里云百炼。
- 认证用户、角色、状态和 token version 存入 Postgres，不再依赖静态账号配置。
- OCR 默认保留 legacy 路径；`OCR_TEXT_MODE=cloud_text` 时 PDF 可走 PaddleOCR 云 API。
- RabbitMQ 只做分发和唤醒，`ingest_tasks` 是权威任务状态。
- QA 使用 LangGraph 做受控工作流，不使用自由 ReAct agent。
- LangChain 使用范围限定在 `langchain-core` tool 包装，不替换现有检索、向量库、图谱或回答生成实现。

## 3. 重要完成记录

### 基础系统

- 配置加载、条件校验和 `.env.example` 已覆盖 OSS、Zvec、Bailian、RabbitMQ、评估等配置；Neo4j 配置保留为后续实验。
- 认证读取 `users` 表，登录审计写入 `auth_audit_logs`，token 校验会检查用户状态和 `token_version`。
- 统一错误响应、trace id 和敏感信息脱敏已接入。

### 文档导入

- 上传、重复文件识别、类型/大小校验已实现。
- 文本、表格、PDF、图片处理链路已接入 worker。
- PaddleOCR、视觉观察、LLM 元数据回填、实体抽取、Zvec 索引和全文索引已进入导入链路；Graph RAG 已从当前主链路停用。
- reprocess 失败不会破坏旧 indexed 文档。
- delete 任务清理新检索可见性，并保留历史 citation 元数据。

### RabbitMQ

- 新增 `TaskMessage`、RabbitMQ publisher、worker consumer。
- 上传、重处理、删除均会创建 DB task 并可发布 RabbitMQ 消息。
- `run_once()` 保留为本地调试和 DB 队列兜底入口。
- `consume_forever()` 为常驻 RabbitMQ consumer。

### RAG 和 Agentic QA

- 混合检索支持关键词、结构化、向量、视觉和 rerank；图谱检索暂不启用。并行检索通道已拆成 `KeywordRetrievalAgent`、`VectorRetrievalAgent`、`StructuredRetrievalAgent`、`VisualRetrievalAgent`、`EvidenceMergeAgent`、`RerankAgent`（`rag/retrieval/hybrid.py`）。
- retrieval trace 记录 channels、model_config、candidates、rerank、final_evidence。
- `/qa/text` 和 `/qa/image` 进入 Agent Controller。
- 受控多 Agent 已落地为 `rag/agent/agents/` 下的独立 Agent class：`QueryAnalystAgent`、`RouterAgent`、`RetrievalAgent`、`PlanningAgent`、`EvidenceReviewerAgent`、`DomainSpecialistAgent`、`QueryRewriteAgent`、`AnswerWriterAgent`、`SafetyReviewerAgent`，由 LangGraph `graph.py` 连接为固定流程。
- 返回结果包含 `agent_trace`，可回溯每个 Agent 的步骤和决策。

### 前端

- 登录、资料库、上传队列、资料详情、文档预览、Assistant 工作台、证据面板、会话历史已实现。
- 前端测试覆盖主要组件、API helper、上传队列、资料库、证据面板和 assistant 工作台。

## 4. 当前仍保留的后续增强

- 路由层和证据检查层实接结构化 Bailian LLM 判断。
- 证据检查升级为槽位级覆盖：型号、部件、周期、故障码、top evidence 一致性。
- 生成后逐 claim citation 对齐，尤其维修动作、安全、周期、扭矩、油液、配件号。
- LangGraph checkpoint 持久化。
- Agent trace 前端专用调试视图。
- DB-backed scanner：RabbitMQ publish 失败后自动重发 stale queued task。
- 精确 bounding box、图片相似检索、复杂图纸关系抽取。
- 完整评估管理 UI。
- 移动端或桌面端打包。

## 5. 文档整理决策

保留 8 个文档：

- `docs/README.md`
- `docs/prd.md`
- `docs/tech-design.md`
- `docs/api-spec.md`
- `docs/database-design.md`
- `docs/deployment.md`
- `docs/ux-spec.md`
- `docs/history.md`

删除旧文档：

- 旧大写命名文档。
- `requirements.md`、`spec.md`、`tasks.md` 的重复规划内容。
- `archive/` 中已完成前端改造草案。
- `superpowers/` 中临时实施计划和设计 spec。

后续新增文档应优先合并到上述 8 份之一，除非出现新的长期维护主题。
