# AgroMech 文档

本目录只保留当前项目需要维护的核心文档。文档内容以当前代码状态为准，避免继续维护旧草案、重复设计稿和临时实施计划。

## 文档清单

- [prd.md](prd.md)：产品目标、用户、已实现能力、边界和验收口径。
- [tech-design.md](tech-design.md)：当前后端、worker、RAG、受控多 Agent QA（第 6 节）、部署和运维设计。
- [api-spec.md](api-spec.md)：当前真实后端接口、请求响应、权限和错误码。
- [database-design.md](database-design.md)：当前关系表、状态机、暂存图谱表和索引数据结构。
- [deployment.md](deployment.md)：静态前端、Docker 后端/worker 和 GitHub Actions 部署说明。
- [ux-spec.md](ux-spec.md)：当前前端页面、交互、角色权限和主要状态。
- [history.md](history.md)：关键决策、完成记录、当前测试基线和后续增强项。

## 当前系统状态

AgroMech 是面向农机资料的多模态 RAG 应用，当前已具备：

- 登录和角色权限：用户存入 Postgres `users` 表，支持 `admin`、`maintainer`、`user`、`evaluator`，登录写入 `auth_audit_logs`。
- 资料库：上传、筛选、详情、预览、重新处理、删除。
- Worker 导入链路：文本、表格、PDF 页面、图片、OCR、视觉观察、LLM 元数据回填、实体、全文索引和 pgvector 向量索引。
- RabbitMQ 上传分发：API 创建 DB task 后可发布消息，worker 可常驻消费；DB `ingest_tasks` 仍是权威状态。
- 文本和图片问答：`/qa/text`、`/qa/image` 均进入 Agent Controller，返回 citations、trace 和 agent_trace。
- 检索链路：关键词、结构化、PostgreSQL + pgvector 向量、Vision RAG、rerank 和降级 trace；Graph RAG 暂不在主链路启用。
- 前端：assistant-ui 问答工作台、资料库、上传队列、证据面板、文档预览、会话历史。

## 当前验证状态

后端和 worker 当前通过：

```bash
.venv/bin/python -m pytest backend/tests worker/tests -q
```

当前分支最近验证结果：`364 passed, 6 warnings`。

`scripts/lint.sh` 当前无 error，但 frontend 有一个既有 warning：`anonymous-chat-store.test.ts` 中 `vi` 未使用。

当前 frontend 基线仍有待修复项：

- `npm run test --prefix frontend` 失败 6 个测试：`anonymous-chat-store.test.ts` 的 `window is not defined`，以及 `agromech-chat.test.ts` 的无 token 错误期望不匹配。
- `npm run build --prefix frontend` 在 `/` 静态预渲染时失败：assistant-ui `ThreadHistoryAdapter` 缺少 `withFormat`。
- 因此 `scripts/test-all.sh` 当前会在后端/worker 通过后停在 frontend 阶段。

## 本地启动摘要

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"

cd ../infrastructure
docker compose --env-file env/.env -f compose/docker-compose.core.yml up -d postgres
docker compose --env-file env/.env -f compose/docker-compose.mq.yml up -d rabbitmq
cd ../AgroMech

.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/create-user.py --username admin --role admin --display-name "Administrator"
.venv/bin/python scripts/rebuild-vector-index.py
.venv/bin/python -m uvicorn agromech_api.main:app --app-dir backend --host 0.0.0.0 --port 8000
.venv/bin/python -m agromech_worker.main
npm run dev --prefix frontend
```

常驻 RabbitMQ worker：

```bash
.venv/bin/python -c "from agromech_worker.main import consume_forever; consume_forever()"
```

`.venv/bin/python -m agromech_worker.main` 是一次性 DB 队列调度入口；`consume_forever()` 是 RabbitMQ 常驻消费入口。
