# 技术设计

## 1. 技术栈

- 后端：FastAPI、SQLAlchemy Core、Alembic、pytest。
- Worker：Python 后台进程，复用后端模型和 ingestion 模块。
- 前端：Next.js App Router、React、TypeScript、Tailwind CSS、assistant-ui。
- 数据库：Postgres，复用 `../infrastructure`。
- 队列：RabbitMQ，复用 `../infrastructure/compose/docker-compose.mq.yml`。
- 图谱：当前主链路暂不启用；Neo4j 和本地图谱代码保留为后续增强。
- 向量：PostgreSQL + pgvector，文本和视觉向量与业务数据同库。
- 文件：local fallback 或阿里云 OSS。
- 模型：阿里云百炼用于 LLM、embedding、vision、rerank；PaddleOCR 云 API 可用于 OCR 文本模式。
- Agentic QA：LangGraph 编排；langchain-core tool 包装检索工具。

## 2. 总体架构

```text
Frontend
  -> FastAPI API
     -> Documents / Auth / Chat Sessions / QA / Retrieval Trace
     -> Postgres
     -> Postgres / pgvector
     -> Bailian / PaddleOCR
  -> Worker
     -> IngestTaskRunner
     -> Text/Table/Image/OCR/Vision/LLM metadata/Entity/Index pipeline
  -> RabbitMQ
     -> wakes worker; DB ingest_tasks remains source of truth
```

后端包结构：

```text
backend/agromech_api/
  main.py                  # FastAPI app 入口
  core/                    # 配置、数据库、错误、依赖健康检查
  security/                # 认证、token、角色权限
  sessions/                # 会话 API 和问答消息历史
  domain/                  # 领域实体抽取、型号别名归一
  evaluation/              # 评测数据集 runner
  api/                     # auth/health 等薄路由
  db/                      # SQLAlchemy Core 表和枚举
  documents/               # 资料库上传、查询、详情服务
  ingestion/               # 文档解析、OCR、视觉观察、元数据回填
  integrations/            # 外部服务适配器、队列、存储、模型服务
  qa/                      # /qa/text 和 /qa/image 路由及响应组织
  rag/                     # Agent、检索、生成、LangChain/LangGraph 组件
```

## 3. 上传和导入链路

```text
POST /documents
  -> validate / deduplicate / store file
  -> insert documents
  -> insert ingest_tasks(status=queued)
  -> optional RabbitMQ publish TaskMessage
  -> worker run_once or consume_forever
  -> process_ingest_task
  -> text/table/image/OCR/vision/LLM metadata/entity/index
  -> document indexed / failed / dead / deleted
```

关键实现：

- `backend/agromech_api/integrations/queue/task_queue.py`：`TaskMessage`、Noop/InMemory/RabbitMQ publisher。
- `backend/agromech_api/documents/routes.py`：上传、重处理、删除后发布 task message。
- `backend/agromech_api/documents/service.py`：文档库查询、文档详情和导入状态服务。
- `worker/agromech_worker/rabbitmq.py`：消费 RabbitMQ 消息，调用 runner，按 DB 状态 ack/nack。
- `worker/agromech_worker/main.py`：`run_once()` 一次性 DB 调度；`consume_forever()` 常驻 RabbitMQ 消费。

可靠性规则：

- `ingest_tasks` 是权威状态，RabbitMQ 只是唤醒和分发。
- malformed message 不重排，基础设施级失败才 nack/requeue。
- duplicate/stale message 由 DB 状态机幂等吸收。
- RabbitMQ publish 关闭时仍可用 `run_once()` 处理 queued task。

## 4. Worker 处理策略

`process_ingest_task()` 按文件类型分发：

- 表格类：`process_table_document()`。
- 图片类：`process_image_document()` + `process_visual_observations()`。
- PDF：优先表格抽取和文本抽取，再渲染页面、OCR、视觉观察。
- `OCR_TEXT_MODE=cloud_text` 且 PDF：走 PaddleOCR 云 API，持久化 page/region 视觉资产，并生成 text/table/image 证据。
- 其他文本类：`process_text_document()`。

所有成功导入继续执行：

- `backfill_document_metadata()` 使用 LLM 回填空的品牌、型号、类型、语言和来源字段。
- `process_document_entities()`。
- `SearchIndexer.index_document()` 为每个可检索 chunk 写入 `chunk_search_index` 和 `chunk_vector_embeddings`；PostgreSQL 上由 `pg_search` 的 BM25 索引提供词法召回。

Graph RAG / Neo4j sync 当前不在主导入链路启用，相关模块暂存为后续增强。

失败通过 `IngestFailure(stage=..., code=...)` 映射到 task/document 状态。

## 5. RAG 检索设计

当前主路径为 `Query Rewrite -> Dense + BM25 -> RRF -> Rerank -> Citation`，由 `hybrid_retrieve_with_trace()` 组织：

- Query Rewrite：在首轮检索前执行；保留型号、故障码、零件号等受保护标识符，失败时使用规则回退并记录 provider、model、耗时和回退原因。
- Dense：使用 embedding 生成查询向量，在 pgvector 表中召回文本 chunk。
- BM25：使用 `pg_search` 的 `ix_chunk_search_index_bm25` 召回词法候选；文档元数据 filters 同时限制 Dense 与 BM25 的查询范围。
- RRF：对 Dense 和 BM25 的名次做 Reciprocal Rank Fusion，不比较两条通道原始分数；`RRF_K` 和两条通道权重可配置。
- Rerank：对融合候选重排；服务异常时使用确定性回退并写入 trace。
- Citation：只允许最终 evidence 中可访问、通过证据准入的 chunk 生成引用；证据不足时不伪造引用。

图片 OCR、视觉描述和实体线索会补充查询与过滤条件，但 Vision RAG 不参与这条文本证据主检索通道。Graph RAG 明确禁用，不能作为当前召回或回答依据。

最终证据：

- 按 `FINAL_EVIDENCE_LIMIT` 裁剪。
- Citation 保留 `document_id`、`document_title`、`chunk_id`、`source_locator`、`evidence_snippet`、`evidence_type`、`accessible`。
- trace 按检索轮次写入 query、filters、Query Rewrite、Dense/BM25 通道、RRF fusion、candidates、Rerank 和 final_evidence；普通角色只能获取脱敏摘要。

## 6. Agentic QA 设计

`/qa/text` 和 `/qa/image` 均进入 `AgentController`，由 LangGraph 编排一组受控 Agent。系统不是自由对话或自由 ReAct agent：流程固定、每个 Agent 只负责一个决策边界、输入输出结构化、都写入 `agent_trace`。核心约束是可信和可追溯，最终回答必须受 `EvidenceReviewerAgent` 和 `SafetyReviewerAgent` 双重约束。

### 6.1 Agent 契约

进程内 Agent 契约位于 `backend/agromech_api/rag/agent/agents/base.py`：

```python
class AgentResult(TypedDict):
    status: str
    output: dict[str, Any]
    trace: dict[str, Any]


class BaseAgent(Protocol):
    name: str

    def run(self, state: AgentState) -> AgentResult:
        ...
```

统一 trace 字段包含 `agent`、`step`、`status`、`decision`、`reason` 等，可在 `agent_trace` 中回溯每一步来源。当前所有 Agent 运行在同一 FastAPI 进程内，通过 LangGraph state 传递上下文。契约刻意保持 A2A-ready，但当前阶段不引入网络级 A2A 协议、序列化、鉴权、重试等复杂度。

### 6.2 问答侧 Agent

`backend/agromech_api/rag/agent/agents/` 下的 Agent：

| Agent | 职责 |
| --- | --- |
| `QueryAnalystAgent` | 解析意图、型号、品牌、故障码、部件、安全敏感性 |
| `RouterAgent` | 判断 Text-only / Text+Visual 路径 |
| `RetrievalAgent` | 调用混合检索工具，返回 evidence 和 citation |
| `PlanningAgent` | 判断是否需要补检索、Query Rewrite 或视觉检索 |
| `EvidenceReviewerAgent` | 生成前证据准入：是否为空、是否可访问、是否匹配问题、型号/故障码是否混淆 |
| `DomainSpecialistAgent` | 按问题类型（保养、故障、配件、视觉）稳定回答结构和领域约束 |
| `QueryRewriteAgent` | 在每一轮检索前做受保护标识符安全的 Query Rewrite |
| `AnswerWriterAgent` | 基于最终证据生成回答，支持文本与多模态 |
| `SafetyReviewerAgent` | 生成后安全审查：高风险主题安全提醒、无来源结论拦截、prompt 注入处理 |

### 6.3 编排流程

`backend/agromech_api/rag/agent/graph.py` 用 LangGraph `StateGraph` 连接节点：

```text
parse (QueryAnalyst)
  -> route (Router)
  -> rewrite (Query Rewrite)
  -> retrieve (Dense + BM25 -> RRF -> Rerank)
  -> planner (Planning，可按需内联视觉检索)
  -> [need_query_rewrite 且未达轮次上限] rewrite -> retrieve
  -> evidence_check (EvidenceReviewer)
       -> [sufficient] domain
       -> [insufficient 且未达上限] rewrite -> retrieve
       -> [need_visual] visual_retrieve
  -> domain (DomainSpecialist)
  -> answer (AnswerWriter -> SafetyReviewer)
  -> END
```

补检索轮次由 `MAX_SUPPLEMENTAL_ROUNDS = 2` 控制。`AgentController`（`controller.py`）是路由层调用的编排边界，只负责构造 state、注入 engine/trace_id 并 invoke graph。

模块：

- `backend/agromech_api/rag/agent/state.py`：LangGraph state 与 `agent_trace` 追加。
- `backend/agromech_api/rag/agent/controller.py`：编排边界。
- `backend/agromech_api/rag/agent/graph.py`：LangGraph `StateGraph`。
- `backend/agromech_api/rag/agent/tools.py`：用 `langchain-core` tool 包装检索调用。
- `backend/agromech_api/rag/retrieval/evidence_check.py`：规则证据充足性检查底座。
- `backend/agromech_api/rag/retrieval/query_rewrite.py`：Query Rewrite provider、受保护标识符校验与规则回退。

### 6.4 检索执行与降级

`backend/agromech_api/rag/retrieval/hybrid.py` 在同一轮并行执行 Dense 与 BM25，随后以 RRF 合并、按 chunk 去重并交给 Rerank。Dense 或 BM25 的可选通道失败不会阻断已有的另一通道，但 trace 会记录状态、耗时与降级原因；两条通道均不可用时不生成无依据 Citation。当前没有关键词加权、结构化召回通道或通道原始分数加权。

### 6.5 当前实现边界

- 不是自由 ReAct agent，不让 LLM 任意选择工具。
- `RouterAgent` 保留可注入的 LLM seam（`llm_router`），默认走规则判断；证据检查同理，LLM 模糊判断是后续增强。
- 生成前 guard 只要求 evidence/citation 支撑；生成后逐 claim citation 对齐（维修动作、安全、周期、扭矩、油液、配件号）是后续增强。
- LangGraph checkpoint 持久化和 `agent_trace` 前端专用调试视图是后续增强。

## 7. 运行和部署

本地依赖：

```bash
cd ../infrastructure
docker compose --env-file env/.env -f compose/docker-compose.core.yml up -d postgres
docker compose --env-file env/.env -f compose/docker-compose.mq.yml up -d rabbitmq
cd ../AgroMech
```

API：

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/create-user.py --username admin --role admin --display-name "Administrator"
.venv/bin/python -m uvicorn agromech_api.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

Worker：

```bash
.venv/bin/python -m agromech_worker.main
.venv/bin/python -c "from agromech_worker.main import consume_forever; consume_forever()"
```

Frontend：

```bash
npm run dev --prefix frontend
```

前端使用 Next 静态导出。登录页和业务请求通过 `/backend/*` 调用 FastAPI；宿主 Nginx 负责把 `/backend/` 反代到 API 容器。聊天不依赖 Next `/api/chat`，浏览器直接调用 `/backend/qa/text` 或 `/backend/qa/image`。

## 8. 关键配置

- `AUTH_TOKEN_SECRET`
- `FILE_STORAGE_BACKEND=local|oss`
- `RABBITMQ_PUBLISH_ENABLED=false|true`
- `RABBITMQ_URL`
- `OCR_TEXT_MODE=legacy|cloud_text`
- Postgres 容器需安装 `vector` 和 `pg_search`；迁移会创建两项扩展及 `ix_chunk_search_index_bm25`
- 迁移后既有 indexed 文档可运行 `.venv/bin/python scripts/rebuild-vector-index.py` 重建 `chunk_search_index`、`chunk_vector_embeddings` 并校验 BM25 索引
- `GRAPH_BACKEND=local`，当前默认不启用 Graph RAG
- `MODEL_PROVIDER=bailian`
- `EMBEDDING_PROVIDER=bailian`
- `RERANK_ENABLED=true`
- `BM25_TOP_K`、`DENSE_TOP_K`、`RRF_K`、`RRF_DENSE_WEIGHT`、`RRF_BM25_WEIGHT`、`FUSION_TOP_K`
- `QUERY_REWRITE_ENABLED`、`QUERY_REWRITE_MODEL`、`QUERY_REWRITE_TIMEOUT_SECONDS`
- `FINAL_EVIDENCE_LIMIT=5`
- `MAX_IMAGES_PER_QUESTION=1`

认证读取数据库 `users` 表；`AUTH_TOKEN_SECRET` 只用于 token 签名。旧的 `AUTH_MODE`、`ADMIN_USERNAME`、`ADMIN_PASSWORD` 等静态账号环境变量不再参与运行时登录。

## 9. 运维检查

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/dependencies
curl -i http://127.0.0.1:8000/health/ready
```

常见排障：

- 文档导入失败：看 task `stage/error_code/error_message`，确认文件类型、OCR、metadata、embedding、index 阶段。
- RabbitMQ 不消费：确认 `infra-rabbitmq`、`RABBITMQ_URL`、`RABBITMQ_PUBLISH_ENABLED` 和 `consume_forever()`。
- 回答无引用：检查 retrieval trace 的 final_evidence、rerank 阈值和 citations。
- 检索就绪失败：确认 Postgres 镜像已安装 `vector` 和 `pg_search`、Alembic 已执行到 head，且 `ix_chunk_search_index_bm25` 存在。
- 向量召回为空：确认文档已完成索引；迁移后可运行 `.venv/bin/python scripts/rebuild-vector-index.py` 重建既有文档向量。
- Bailian 失败：检查 API key、base URL、限流和 trace 中 degraded channel。

## 10. 评估

评估 runner 入口是 Python 函数 `run_evaluation_dataset()`。题库存储在 `evaluation_questions`，运行结果写入 `evaluation_runs`。

当前指标口径：

- top-5 来源命中率。
- citation correctness。
- model confusion rate。
- safety warning coverage。
- failure_types：`evidence_insufficient`、`source_miss`、`model_confusion`、`safety_missing` 等。
