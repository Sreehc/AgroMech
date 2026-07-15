# 数据库设计

本文以 `backend/agromech_api/db/models.py` 和 Alembic 迁移为准。

## 1. 核心状态

文档状态：

- `queued`
- `processing`
- `reprocessing`
- `indexed`
- `failed`
- `deleting`
- `deleted`

任务类型：

- `ingest`
- `reprocess`
- `delete`

任务状态：

- `queued`
- `processing`
- `succeeded`
- `failed`
- `dead`
- `cancelled`

chunk 类型：

- `text`
- `table`
- `image`

asset 类型：

- `page_image`
- `source_image`
- `extracted_image`

## 2. 文档和导入

### `documents`

资料主表。关键字段：

- `id`
- `title`
- `original_file_name`
- `file_hash`
- `file_size_bytes`
- `mime_type`
- `storage_uri`
- `brand`
- `model`
- `document_type`
- `language`
- `document_version`
- `source`
- `status`
- `failure_stage`
- `failure_code`
- `failure_message`
- `created_by_role`
- `created_at`
- `updated_at`
- `deleted_at`

索引：`status`、`brand/model`、`file_hash`。

### `ingest_tasks`

导入、重处理、删除任务表，也是 worker 状态机的权威来源。关键字段：

- `id`
- `document_id`
- `task_type`
- `status`
- `attempt_count`
- `stage`
- `error_code`
- `error_message`
- `started_at`
- `finished_at`

### `document_assets`

页面图、原始图片、提取图片等视觉资产。关键字段：

- `document_id`
- `asset_type`
- `storage_uri`
- `mime_type`
- `page_number`
- `source_locator`
- `ocr_text`
- `visual_observation`

### `document_chunks`

可检索证据单元。关键字段：

- `document_id`
- `asset_id`
- `chunk_type`
- `content`
- `summary`
- `page_number`
- `section_title`
- `worksheet_name`
- `row_start`
- `row_end`
- `source_locator`
- `metadata`

`source_locator` 必填，用于 citation 和预览定位。

## 3. 认证和审计

### `users`

数据库用户表。关键字段：

- `id`
- `username`
- `password_hash`
- `role`: `admin`、`maintainer`、`user`、`evaluator`
- `status`: `active`、`disabled`
- `display_name`
- `last_login_at`
- `password_changed_at`
- `token_version`
- `created_at`
- `updated_at`

`username` 唯一。登录、权限判断和 token 校验均读取本表；`token_version` 增加后，旧 token 会失效。

### `auth_audit_logs`

登录审计表。关键字段：

- `user_id`
- `username`
- `event_type`
- `success`
- `ip_address`
- `user_agent`
- `metadata`
- `created_at`

密码错误、禁用用户和未知用户都会写入登录审计。

## 4. 索引和检索

### `chunk_search_index`

Dense + BM25 检索的词法侧索引引用。包含：

- `chunk_id`
- `document_id`
- `chunk_type`
- `search_text`
- `embedding_version`
- `chunk_profile`
- `embedding_dimension`

`chunk_id + embedding_version` 唯一。PostgreSQL 使用 `pg_search` 的 `ix_chunk_search_index_bm25`（访问方法 `bm25`）在 `search_text` 上提供 BM25 召回；`embedding_version`、`chunk_profile` 和 `embedding_dimension` 与 `chunk_vector_embeddings` 对齐，用于检索 trace 和重建校验。

### `chunk_vector_embeddings`

文本 chunk 向量表。embedding 直接保存在 Postgres pgvector 中，并通过 `chunk_id` 回连 `document_chunks`：

- `chunk_id`
- `document_id`
- `provider`
- `model`
- `embedding_version`
- `chunk_profile`
- `embedding_dimension`
- `embedding vector(1024)`
- `status`

`chunk_id + embedding_version` 唯一；`document_id` 用于文档级清理和过滤；pgvector HNSW 索引用于 cosine 召回。`embedding_version` 标记模型、维度和 chunk profile，便于重建和灰度切换。

### `visual_page_vector_embeddings`

PDF 页面渲染图、图片和 OCR 视觉资产的页面级向量表。embedding 直接保存在 Postgres pgvector 中，并通过 `asset_id` 回连 `document_assets`：

- `asset_id`
- `document_id`
- `page_number`
- `provider`
- `model`
- `embedding_version`
- `embedding_dimension`
- `embedding vector(1024)`
- `status`

`asset_id + embedding_version` 唯一；`document_id` 用于文档级清理和过滤；pgvector HNSW 索引用于视觉页召回。`embedding_version` 标记视觉 embedding 模型和页面索引版本。

### `retrieval_logs`

检索 trace 表。包含：

- `trace_id`
- `query`
- `filters`
- `query_rewrite`
- `fusion`
- `channels`
- `model_config`
- `candidates`
- `rerank`
- `final_evidence`

`query_rewrite` 保存每轮 Query Rewrite 的原问题、重写问题、provider/model、回退状态和受保护标识符校验结果。`fusion` 保存 Dense + BM25 的 RRF 参数、通道数量和候选名次。`final_evidence` 是 Citation 唯一允许使用的证据集合。

`trace_id` 唯一。

## 5. 实体和暂存图谱表

### `chunk_entity_links`

chunk 到领域实体的链接：

- `chunk_id`
- `document_id`
- `entity_type`
- `entity_value`
- `normalized_value`
- `confidence`
- `source`

用于结构化过滤；图谱同步当前不在主链路启用。

### `document_entity_extractions`

文档级抽取结果快照：

- `document_id`
- `extracted_entities`
- `confidence`
- `low_confidence`

### `graph_nodes`

本地图谱节点缓存，当前作为后续 Graph RAG 预留表：

- `entity_type`
- `entity_value`
- `normalized_value`

`entity_type + normalized_value` 唯一。

### `graph_edges`

本地图谱边缓存，当前作为后续 Graph RAG 预留表：

- `source_node_id`
- `target_node_id`
- `source_entity_type`
- `source_entity_value`
- `target_entity_type`
- `target_entity_value`
- `relationship_type`
- `source_document_id`
- `source_chunk_id`
- `schema_version`
- `confidence`
- `is_active`
- `valid_to`

如果后续恢复 Graph RAG，图谱边必须保留来源文档，最终回答不能只引用图谱边，必须回到 chunk。

当前常见实体类型：

- brand
- model
- subsystem
- component
- fault_code
- fault_symptom
- part_number
- maintenance_item
- document
- chunk

当前关系用于 1-2 跳扩展，重处理和删除会让旧边 inactive。

## 6. 问答和引用

### `qa_records`

问答记录：

- `trace_id`
- `question`
- `answer`
- `sections`
- `uncertainty`

### `answer_citations`

回答引用：

- `qa_record_id`
- `document_id`
- `chunk_id`
- `citation_payload`
- `accessible`

删除资料后，`document_id/chunk_id` 可置空，但 `citation_payload` 保留历史元数据和不可访问提示。

## 7. 会话

### `chat_sessions`

- `username`
- `title`
- `messages`
- `filters`
- `has_image`
- `created_at`
- `updated_at`

### `qa_messages`

- `session_id`
- `role`
- `content`
- `metadata`
- `created_at`

文本问答和图片问答携带 `session_id` 时会追加用户消息和 assistant 消息。

## 8. 型号别名

### `model_aliases`

- `alias`
- `normalized_alias`
- `canonical_model`
- `normalized_canonical`
- `status`: `active`、`candidate`、`rejected`
- `source`: `manual`、`rule`、`llm`
- `confidence`
- `notes`

LLM 候选不会直接进入正式别名，必须提升为 active。

## 9. 评估

### `evaluation_questions`

固定题库：

- `question_id`
- `dataset_version`
- `category`
- `question`
- `expected_model`
- `expected_answer_summary`
- `expected_sources`
- `requires_safety_warning`
- `must_not_include`

`question_id + dataset_version` 唯一。

### `evaluation_runs`

评估运行记录：

- `run_id`
- `dataset_version`
- `model_config`
- `prompt_version`
- `code_version`
- `metrics_summary`
- `failure_types`
- `started_at`
- `finished_at`
