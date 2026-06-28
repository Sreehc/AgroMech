# 技术设计

## 1. 技术栈

- 后端：FastAPI、SQLAlchemy Core、Alembic、pytest。
- Worker：Python 后台进程，复用后端模型和 ingestion 模块。
- 前端：Next.js App Router、React、TypeScript、Tailwind CSS、assistant-ui。
- 数据库：Postgres，复用 `../infrastructure`。
- 队列：RabbitMQ，复用 `../infrastructure/compose/docker-compose.mq.yml`。
- 图谱：当前主链路暂不启用；Neo4j 和本地图谱代码保留为后续增强。
- 向量：Zvec，本项目 `.agromech-data/zvec`。
- 文件：local fallback 或阿里云 OSS。
- 模型：阿里云百炼用于 LLM、embedding、vision、rerank；PaddleOCR 云 API 可用于 OCR 文本模式。
- Agentic QA：LangGraph 编排；langchain-core tool 包装检索工具。

## 2. 总体架构

```text
Frontend
  -> FastAPI API
     -> Documents / Auth / Chat Sessions / QA / Retrieval Trace
     -> Postgres
     -> Zvec
     -> Bailian / PaddleOCR
  -> Worker
     -> IngestTaskRunner
     -> Text/Table/Image/OCR/Vision/LLM metadata/Entity/Index pipeline
  -> RabbitMQ
     -> wakes worker; DB ingest_tasks remains source of truth
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

- `backend/agromech_api/task_queue.py`：`TaskMessage`、Noop/InMemory/RabbitMQ publisher。
- `backend/agromech_api/documents.py`：上传、重处理、删除后发布 task message。
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
- `SearchIndexer.index_document()` 写全文索引、embedding reference 和 Zvec。

Graph RAG / Neo4j sync 当前不在主导入链路启用，相关模块暂存为后续增强。

失败通过 `IngestFailure(stage=..., code=...)` 映射到 task/document 状态。

## 5. RAG 检索设计

混合检索由 `hybrid_retrieve_with_trace()` 组织：

- query understanding：解析意图、型号、品牌、系统、部件、故障码、配件号和安全敏感性。
- keyword：精确匹配型号、故障码、配件号、标题、表格字段。
- structured：按文档元数据和实体链接过滤/加权。
- vector：使用百炼 embedding 查询 Zvec。
- vision：图片 OCR、视觉描述和实体线索进入文本检索链路。
- rerank：百炼 rerank，失败时 deterministic fallback，并写入 trace。

最终证据：

- 按 `FINAL_EVIDENCE_LIMIT` 裁剪。
- citation 保留 `document_id`、`document_title`、`chunk_id`、`source_locator`、`evidence_snippet`、`evidence_type`、`accessible`。
- trace 写入 query、filters、channels、model_config、candidates、rerank、final_evidence。

## 6. Agentic QA 设计

当前 `/qa/text` 和 `/qa/image` 均进入 `AgentController`：

```text
parse_query
  -> route_question
  -> retrieve tool
  -> evidence_check
  -> rewrite if needed, max 2 supplemental rounds
  -> generation_guard
  -> answer generation
```

模块：

- `agent_state.py`：LangGraph state。
- `agent_router.py`：规则优先 Text-only / Text+Visual 路由，保留可注入 LLM seam。
- `agent_tools.py`：用 `langchain-core` tool 包装检索调用。
- `agent_graph.py`：LangGraph `StateGraph`。
- `agent_controller.py`：路由模块调用的编排边界。
- `evidence_check.py`：规则证据充足性检查。
- `query_rewrite.py`：确定性领域同义词扩展。

当前实现边界：

- 不是自由 ReAct agent，不让 LLM 任意选择工具。
- 路由和证据检查默认规则判断；LLM 模糊判断是后续增强。
- 生成前 guard 只要求 evidence/citation 支撑；生成后逐 claim citation 对齐是后续增强。

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

## 8. 关键配置

- `AUTH_MODE=single_admin|static_roles`
- `FILE_STORAGE_BACKEND=local|oss`
- `RABBITMQ_PUBLISH_ENABLED=false|true`
- `RABBITMQ_URL`
- `OCR_TEXT_MODE=legacy|cloud_text`
- `VECTOR_BACKEND=zvec`
- `GRAPH_BACKEND=local`，当前默认不启用 Graph RAG
- `MODEL_PROVIDER=bailian`
- `EMBEDDING_PROVIDER=bailian`
- `RERANK_ENABLED=true`
- `FINAL_EVIDENCE_LIMIT=5`
- `MAX_IMAGES_PER_QUESTION=1`

## 9. 运维检查

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/dependencies
```

常见排障：

- 文档导入失败：看 task `stage/error_code/error_message`，确认文件类型、OCR、metadata、embedding、index 阶段。
- RabbitMQ 不消费：确认 `infra-rabbitmq`、`RABBITMQ_URL`、`RABBITMQ_PUBLISH_ENABLED` 和 `consume_forever()`。
- 回答无引用：检查 retrieval trace 的 final_evidence、rerank 阈值和 citations。
- Zvec 异常：检查 `ZVEC_PATH`、collection、embedding dimension/version。
- Bailian 失败：检查 API key、base URL、限流和 trace 中 degraded channel。

## 10. 评估

评估 runner 入口是 Python 函数 `run_evaluation_dataset()`。题库存储在 `evaluation_questions`，运行结果写入 `evaluation_runs`。

当前指标口径：

- top-5 来源命中率。
- citation correctness。
- model confusion rate。
- safety warning coverage。
- failure_types：`evidence_insufficient`、`source_miss`、`model_confusion`、`safety_missing` 等。
