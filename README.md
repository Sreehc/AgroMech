# AgroMech RAG

AgroMech RAG 是一个面向农机知识的检索增强生成系统，目标是把农机说明书、维修手册、故障码表、保养规程、配件目录、图纸、扫描件和现场维修记录整理成可检索、可引用、可追溯的知识库。

项目当前处于设计和早期开发阶段。第一版重点不是做一个通用聊天机器人，而是做一个垂直的农机资料检索与问答助手。

## 目标用户

- 农机维修人员。
- 农机经销商和售后服务人员。
- 合作社、农场、农机服务组织的技术负责人。
- 农业机械方向的学生、教师和研究人员。
- 需要搭建私有农机知识库的团队。

## 核心能力

- 上传农机资料并在后台解析。
- 识别文本、表格、扫描件、图片和图纸。
- 使用关键词检索、向量检索、结构化检索、Graph RAG、Vision RAG 和 rerank 组合召回证据。
- 支持按品牌、型号、部件、故障码、故障现象、保养项目提问。
- 支持图像辅助查询，例如仪表盘照片、故障灯、部件照片、液压图、电路图。
- 基于来源资料生成带引用的回答。
- 对维修、安全、适用型号等内容给出边界说明。
- 记录检索链路和评估结果，方便持续改进。

## MVP 范围

第一版可用系统应支持：

1. 上传 PDF、Word、Excel/CSV、TXT/Markdown 和常见图片。
2. 抽取文本、表格、图片描述和农机领域元数据。
3. 建立全文索引和向量索引。
4. 抽取知识图谱实体和关系。
5. 对多路召回候选证据进行 rerank。
6. 支持按型号、系统、故障现象、故障码或图片提问。
7. 返回有来源引用的回答。
8. 跟踪资料处理状态、检索链路和评估结果。

## 文档

开发文档位于 [docs](docs/README.md)。

建议阅读顺序：

1. [产品需求文档](docs/PRD.md)
2. [系统架构文档](docs/ARCHITECTURE.md)
3. [RAG 设计文档](docs/RAG_DESIGN.md)
4. [数据模型文档](docs/DATA_MODEL.md)
5. [知识图谱 Schema](docs/GRAPH_SCHEMA.md)
6. [Vision RAG 设计文档](docs/VISION_RAG.md)
7. [评估文档](docs/EVALUATION.md)
8. [API 草案](docs/API.md)
9. [部署文档](docs/DEPLOYMENT.md)
10. [运维手册](docs/RUNBOOK.md)
11. [Prompt 文档](docs/PROMPTS.md)
12. [已确认决策](docs/DECISIONS.md)

## 规划架构

```text
前端
  -> 后端 API
    -> 资料导入 Worker
    -> 检索服务
    -> RAG 回答服务
    -> Vision 服务
    -> Graph 服务
  -> 数据库 / 文件存储 / 向量索引 / 图谱存储
```

已确认技术栈：

- 后端：Python、FastAPI、SQLAlchemy、Alembic。
- 前端：React、Vite、TypeScript。
- 数据库：Postgres。
- 向量检索：Milvus。
- 文件存储：先用本地文件系统，后续支持 MinIO/S3。
- OCR 和视觉：PaddleOCR，再接可配置的视觉语言模型。
- LLM / embedding：阿里云百炼。

## 安全原则

AgroMech RAG 不应输出没有来源支持的维修结论。涉及液压高压、电气系统、发动机、制动系统和旋转部件时，回答必须包含来源依据、适用范围和安全提醒。

## MVP 默认配置

第一版开发按根目录 `.env.example` 中的默认值执行，后续可通过环境变量调整。

已固化的 P0 默认决策：

- 权限模式：`AUTH_MODE=single_admin`，默认单一管理员账号，角色矩阵作为后续扩展。
- 上传限制：单文件 100 MB，单图片 20 MB，并发上传 2 个，资料库默认 5 GB。
- 删除策略：`DOCUMENT_DELETE_MODE=soft_delete`，默认标记删除并隐藏资料，同时清理检索可见性；历史引用保留不可访问提示。
- 表格型 PDF：`TABLE_PDF_MODE=text_or_ocr`，P0 中仅作为文本或 OCR 内容进入检索，不做结构化表格验收。
- 评估形态：`EVALUATION_RUNNER_MODE=cli`，P0 先提供 runner 能力，完整评估管理页面后置。
- 图片提问：P0 单次只支持 1 张图片，默认视觉低置信阈值为 `0.55`。
- 降级策略：Postgres、Milvus、embedding 和 LLM 为 P0 必需；Graph、Vision、rerank 通道异常时允许降级，但必须写入检索 trace。

## 本地开发

后端、worker 和前端已拆成独立目录：

```text
backend/   FastAPI API
worker/    异步任务 worker
frontend/  React + Vite + TypeScript 前端
```

初始化 Python 开发环境：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

启动本地依赖服务：

```bash
docker compose up -d postgres milvus neo4j
```

启动后端 API：

```bash
.venv/bin/python -m uvicorn agromech_api.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

检查后端和依赖连通性：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/dependencies
```

后端认证使用 MVP 默认的单管理员模式。登录后把 `access_token` 作为 bearer token 传给需要登录或写权限的 API：

```bash
curl -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"change-me"}'
curl http://127.0.0.1:8000/auth/me -H "Authorization: Bearer <token>"
```

上传资料会创建 document 记录和导入任务：

```bash
curl -X POST http://127.0.0.1:8000/documents \
  -H "Authorization: Bearer <token>" \
  -F "file=@manual.txt" \
  -F "brand=Kubota" \
  -F "model=M7040"
```

重复文件会返回 `duplicate_of`，不支持类型返回 `unsupported_file_type`，超出配置大小返回 `file_too_large`。

资料库后端 API：

```bash
curl "http://127.0.0.1:8000/documents?brand=Kubota&model=M7040" -H "Authorization: Bearer <token>"
curl http://127.0.0.1:8000/documents/<document_id> -H "Authorization: Bearer <token>"
curl http://127.0.0.1:8000/tasks/<task_id> -H "Authorization: Bearer <token>"
curl -X POST http://127.0.0.1:8000/documents/<document_id>/reprocess -H "Authorization: Bearer <token>"
curl -X DELETE http://127.0.0.1:8000/documents/<document_id> -H "Authorization: Bearer <token>"
```

API 错误响应统一为：

```json
{
  "error": {
    "code": "timeout",
    "message": "Request timed out",
    "details": null,
    "trace_id": "..."
  }
}
```

基础错误码包括 `unauthorized`、`forbidden`、`unsupported_file_type`、`file_too_large` 和 `timeout`。
外部服务连接、检索和 LLM 超时通过 `.env.example` 中的超时配置调整。

运行数据库迁移：

```bash
.venv/bin/python -m alembic upgrade head
```

启动 worker：

```bash
.venv/bin/python -m agromech_worker.main
```

当前 worker 每次启动会执行一次导入队列调度，将最早的 `queued` 任务推进到
`processing`，并从 PDF、DOCX、Markdown、TXT 生成带来源定位的 `text_chunk`，
从 Excel / CSV 生成带工作表和行号定位的 `table_chunk`，为图片和 PDF 页面生成
`document_assets`、OCR `image_chunk` 和视觉观察线索。`reprocess` 和 `delete` 类型任务已接入状态机；
OCR 或视觉模型不可用时会记录失败状态，不伪装为成功；索引写入在后续任务中实现。

初始化并启动前端：

```bash
npm install --prefix frontend
npm run dev --prefix frontend
```

前端开发服务器会把 `/auth/*` 代理到本地后端 `http://127.0.0.1:8000`。

运行当前测试：

```bash
.venv/bin/python -m pytest
npm run test --prefix frontend
npm run build --prefix frontend
```
