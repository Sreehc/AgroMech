# AgroMech RAG

AgroMech 是面向农机资料的多模态检索增强生成系统。它将说明书、维修手册、故障码表、保养规程、配件目录、图纸、扫描页和现场图片转为可检索、可引用、可审计的知识库。

[![CI](https://github.com/Sreehc/AgroMech/actions/workflows/ci.yml/badge.svg)](https://github.com/Sreehc/AgroMech/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)

这不是通用聊天机器人。维修、保养、安全和配件问题的回答必须基于来源证据；证据不足时必须明确不确定性，并且不得伪造 Citation。

## 能力

- 导入 PDF、DOCX、XLSX、CSV、TXT、Markdown、PNG、JPG、JPEG、WEBP 文档。
- Worker 处理文本、表格、PDF 页、OCR、视觉观察、元数据、实体，并建立 `pg_search` BM25 与 pgvector 索引。
- 受控检索主路径：`Query Rewrite -> Dense + BM25 -> RRF -> Rerank -> Citation`。
- 文本与图片问答返回 Citation、检索 trace、`agent_trace` 和不确定性信息。
- PostgreSQL 用户、角色、审计与 RabbitMQ 任务唤醒。
- 本地文件存储用于开发，阿里云 OSS 用于生产。

Graph RAG 和 Neo4j 相关代码仅为后续实验保留，当前产品路径明确不启用 Graph RAG。

## 架构

```text
Next.js 静态前端
  -> /backend/*（Nginx）
     -> FastAPI
        -> Auth / Documents / QA / Retrieval Trace
        -> PostgreSQL：pgvector + pg_search（BM25）
        -> 百炼 / PaddleOCR

RabbitMQ
  -> Worker
     -> 导入、chunk_search_index、chunk_vector_embeddings
```

Dense 与 BM25 在同一轮并行召回，RRF 只比较名次而不比较原始分数。filters 同时限制两条通道的范围，不作为独立加权召回。Query Rewrite 在首轮与补检索轮的检索前执行，保护型号、故障码和零件号；最终 Citation 仅能来自通过证据准入的最终 evidence。

## 目录

```text
backend/agromech_api/   FastAPI、数据库、QA、检索、导入模块
worker/agromech_worker/ Worker 与 RabbitMQ consumer
frontend/               Next.js 前端
backend/alembic/        数据库迁移
scripts/                运维与评估脚本
deploy/                 生产 compose、Nginx 与环境模板
docs/                   维护中的中文产品、API、数据库与部署文档
```

## 环境要求

- Python 3.11+
- 与 Next.js 16 兼容的 Node.js
- 安装 `vector` 和 `pg_search` 扩展的 PostgreSQL
- RabbitMQ
- 生产可选：阿里云 OSS、百炼、PaddleOCR 云 API

本地默认复用同级 `../infrastructure` 仓库提供的 PostgreSQL 与 RabbitMQ，也可自行配置 `.env`。

## 本地启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
```

```bash
cd ../infrastructure
docker compose --env-file env/.env -f compose/docker-compose.core.yml up -d postgres
docker compose --env-file env/.env -f compose/docker-compose.mq.yml up -d rabbitmq
cd ../AgroMech
```

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/create-user.py --username admin --role admin --display-name "Administrator"
.venv/bin/python -m uvicorn agromech_api.main:app --app-dir backend --host 0.0.0.0 --port 8000
.venv/bin/python -m agromech_worker.main
npm install --prefix frontend
npm run dev --prefix frontend
```

常驻 RabbitMQ consumer：

```bash
.venv/bin/python -c "from agromech_worker.main import consume_forever; consume_forever()"
```

健康检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/dependencies
curl -i http://127.0.0.1:8000/health/ready
```

## 索引重建与发布

迁移会创建 `vector`、`pg_search` 与 `ix_chunk_search_index_bm25`。对已有 indexed 文档执行：

```bash
.venv/bin/python scripts/rebuild-vector-index.py
```

该命令重建 `chunk_search_index` 与 `chunk_vector_embeddings`，输出重建行数，并在 PostgreSQL 上确认 BM25 索引存在。生产发布前的顺序为：记录基线、备份、安装扩展、迁移、重建索引、Dense/BM25/RRF 冒烟、部署、`/health/ready`、QA/Citation 冒烟与监控。详情见 [部署文档](docs/deployment.md)。

## 测试与验收

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests worker/tests -q
AGROMECH_TEST_POSTGRES_URL="$AGROMECH_TEST_POSTGRES_URL" scripts/test-integration.sh
npm run test --prefix frontend
npm run build --prefix frontend
```

检索评估使用：

```bash
.venv/bin/python scripts/evaluate-retrieval.py \
  --dataset curated-mvp \
  --baseline /tmp/agromech-retrieval-baseline.json
```

未提供真实生产 `curated-mvp` 数据库时，评估结果只能代表随仓库提供的合成开发数据，不能代替生产验收。

## 文档

- [docs/README.md](docs/README.md)：文档索引与当前系统状态
- [docs/prd.md](docs/prd.md)：产品范围和验收口径
- [docs/tech-design.md](docs/tech-design.md)：架构、检索和 QA 设计
- [docs/api-spec.md](docs/api-spec.md)：运行时 API
- [docs/database-design.md](docs/database-design.md)：表、索引和检索 trace
- [docs/deployment.md](docs/deployment.md)：生产部署与运行检查
- [docs/ux-spec.md](docs/ux-spec.md)：前端 UX
- [docs/history.md](docs/history.md)：决策、验证基线和后续工作

## 许可

仓库目前未提供 `LICENSE` 文件。在添加许可前，保留所有权利。
