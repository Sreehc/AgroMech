# API 规格

本文记录当前真实后端 API。默认地址：

```text
http://127.0.0.1:8000
```

除登录和健康检查外，业务接口需要 `Authorization: Bearer <token>`。

## 1. 通用错误

```json
{
  "error": {
    "code": "question_required",
    "message": "Question is required",
    "details": null,
    "trace_id": "trace-123"
  }
}
```

常见错误码：

- `unauthorized`
- `forbidden`
- `not_found`
- `validation_error`
- `unsupported_file_type`
- `file_too_large`
- `too_many_images`
- `question_required`
- `question_too_long`
- `timeout`
- `internal_error`

## 2. System

### `GET /health`

返回 API 进程状态。

```json
{
  "status": "ok",
  "service": "api",
  "environment": "development"
}
```

### `GET /health/dependencies`

返回依赖状态，当前覆盖 `postgres`、可选 `neo4j`、`file_storage`、`pgvector`、`pg_search`、`bailian`。`pg_search` 检查会同时验证当前 schema 中 `chunk_search_index` 的 `ix_chunk_search_index_bm25` 是否使用 `bm25` 访问方法；`ok` 与 `not_applicable` 都不会使整体降级。

### `GET /health/ready`

发布就绪检查。所有必需依赖为 `ok`、或本地非 PostgreSQL 环境的依赖为 `not_applicable` 时返回 `200`：

```json
{
  "status": "ok",
  "dependencies": [{"name": "pg_search", "status": "ok", "target": "postgres:extension/pg_search"}]
}
```

任何必需依赖不可用时返回 `503` 与 `status: "unavailable"`。部署必须在应用切换前检查此接口。

## 3. Auth

认证读取 Postgres `users` 表。用户状态必须为 `active`，token 校验会比对用户状态、角色和 `token_version`；登录尝试写入 `auth_audit_logs`。首次部署后可用 `scripts/create-user.py` 创建管理员。

### `POST /auth/login`

请求：

```json
{
  "username": "admin",
  "password": "change-me"
}
```

响应：

```json
{
  "access_token": "jwt",
  "token_type": "bearer",
  "expires_in": 43200
}
```

### `GET /auth/me`

返回当前用户：

```json
{
  "username": "admin",
  "role": "admin"
}
```

## 4. Chat Sessions

权限：`admin`、`maintainer`、`user`、`evaluator`。用户只能访问自己的会话。

- `GET /chat-sessions?limit=50`
- `POST /chat-sessions`
- `GET /chat-sessions/{session_id}`
- `PATCH /chat-sessions/{session_id}`
- `DELETE /chat-sessions/{session_id}`

会话对象：

```json
{
  "id": "session-1",
  "title": "液压排查",
  "messages": [],
  "filters": {
    "model": "M7040"
  },
  "has_image": false,
  "created_at": "2026-06-24T09:00:00Z",
  "updated_at": "2026-06-24T09:10:00Z"
}
```

## 5. Documents

### `POST /documents`

权限：`admin`、`maintainer`。

`multipart/form-data` 字段：

- `file`：必填。
- `brand`
- `model`
- `document_type`
- `language`
- `source`

支持扩展名：`pdf`、`docx`、`xlsx`、`csv`、`txt`、`md`、`markdown`、`png`、`jpg`、`jpeg`、`webp`。

响应：

```json
{
  "document_id": "doc-123",
  "task_id": "task-123",
  "status": "queued",
  "duplicate_of": null
}
```

创建 DB task 后会通过配置的 `task_publisher` 发布 RabbitMQ 消息。默认 `RABBITMQ_PUBLISH_ENABLED=false`，不影响 DB 队列处理。

### `GET /documents`

权限：所有登录角色。

查询参数：`brand`、`model`、`document_type`、`language`、`status`。

响应包含 `total` 和 `items`。每项包括文档元数据、状态、更新时间、摘要、最近 task 和 failure。

### `GET /documents/{document_id}`

返回文档详情、最近 task、failure 和 chunk 摘要。删除或不可访问资料会保留基础元数据并标记不可访问。

### `GET /documents/{document_id}/preview`

按 chunk 或文档信息返回文本/PDF 页面预览和 highlight 信息。

### `GET /documents/{document_id}/assets/{asset_id}`

返回文档资产内容或可访问 URL。

### `POST /documents/{document_id}/reprocess`

权限：`admin`、`maintainer`。创建 `reprocess` task，并发布 RabbitMQ 消息。

### `DELETE /documents/{document_id}`

权限：`admin`、`maintainer`。创建 `delete` task，文档进入 `deleting`，worker 成功后清理检索可见索引并置为 `deleted`。

## 6. Tasks

### `GET /tasks/{task_id}`

权限：`admin`、`maintainer`。

返回 task 状态、类型、attempt_count、stage、error_code、error_message、started_at、finished_at。

任务类型：`ingest`、`reprocess`、`delete`。

任务状态：`queued`、`processing`、`succeeded`、`failed`、`dead`、`cancelled`。

## 7. QA

### `POST /qa/text`

权限：所有登录角色。

请求：

```json
{
  "question": "M7040 液压油多久换一次？",
  "filters": {
    "brand": "Kubota",
    "model": "M7040",
    "document_type": "manual",
    "language": "zh-CN"
  },
  "session_id": "session-1",
  "mode": "standard"
}
```

响应：

```json
{
  "answer": "根据来源证据...",
  "sections": {
    "conclusion": "根据来源证据...",
    "citations": ["M7040 Manual / chunk-1"],
    "uncertainty": {
      "level": "low",
      "reasons": []
    }
  },
  "citations": [
    {
      "document_id": "doc-1",
      "document_title": "M7040 Manual",
      "chunk_id": "chunk-1",
      "source_locator": {
        "type": "text",
        "line_start": 1,
        "line_end": 3
      },
      "evidence_snippet": "source text",
      "evidence_type": "text",
      "accessible": true
    }
  ],
  "trace_id": "trace-1",
  "uncertainty": {
    "level": "low",
    "reasons": []
  },
  "safety_warnings": [],
  "agent_trace": [
    {
      "step": "route",
      "decision": "text_only",
      "reason": "text maintenance or parameter wording matched",
      "source": "rule"
    }
  ]
}
```

特殊行为：

- 空问题：`400 question_required`。
- 超长问题：`400 question_too_long`。
- prompt 注入：返回安全拒答。
- 证据不足：`200`，`citations=[]`，`uncertainty.level=high`。

### `POST /qa/image`

权限：所有登录角色。

`multipart/form-data` 字段：

- `image`：必填，只支持 1 张。
- `question`
- `brand`
- `model`
- `document_type`
- `language`
- `session_id`

响应在文本问答字段基础上增加：

```json
{
  "visual_observation": "possible model M7040; visible part hydraulic",
  "ocr_text": "",
  "detected_entities": {
    "possible_models": ["M7040"],
    "visible_parts": ["hydraulic"],
    "warning_lights": [],
    "part_numbers": []
  },
  "visual_annotations": [],
  "visual_annotation_status": {
    "status": "available",
    "coordinate_format": "normalized_xywh",
    "missing_reason": null
  },
  "visual_confidence": {
    "confidence": 0.8,
    "low_confidence": false,
    "ocr_status": "succeeded"
  },
  "agent_trace": [
    {
      "step": "route",
      "decision": "text_visual",
      "reason": "visual input is present",
      "source": "rule"
    }
  ]
}
```

特殊行为：

- 多图：`400 too_many_images`。
- 类型不支持：`415 unsupported_file_type`。
- 图片过大：`413 file_too_large`。
- 低置信且无问题：返回补充信息提示，不强行给结论。

## 8. Retrieval Trace

### `GET /retrieval-traces/{trace_id}`

权限：所有登录角色。`admin`、`evaluator` 可看完整调试信息；其他角色只看脱敏摘要。

摘要字段：

- `trace_id`
- `query`
- `filters`
- `channels`
- `model_config`
- `created_at`

完整响应额外包含：

- `query_rewrite`
- `fusion`
- `candidates`
- `rerank`
- `final_evidence`

其中 `query_rewrite` 展示最终 Query Rewrite 的 provider 和回退状态，`fusion` 展示 Dense + BM25 的 RRF 摘要。敏感字段、内部路径、异常栈会脱敏；普通角色不会得到候选内容或 Citation 审计细节。
