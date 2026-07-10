# AgroMech RAG

AgroMech is a multimodal retrieval-augmented generation system for agricultural machinery documentation. It turns manuals, service guides, fault-code tables, maintenance procedures, parts catalogs, drawings, scanned pages, and field images into a searchable knowledge base with citations and traceable evidence.

[![CI](https://github.com/Sreehc/AgroMech/actions/workflows/ci.yml/badge.svg)](https://github.com/Sreehc/AgroMech/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Next.js](https://img.shields.io/badge/Next.js-16-000000?logo=next.js&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)

AgroMech is not a general chatbot. It is a domain assistant for evidence-bound repair, maintenance, safety, and parts questions. Answers are expected to cite source chunks, expose uncertainty when evidence is weak, and keep safety warnings for hydraulic, electrical, engine, braking, and rotating-component work.

## Features

- Document ingestion for PDF, DOCX, XLSX, CSV, TXT, Markdown, PNG, JPG, JPEG, and WEBP.
- Worker pipeline for text, tables, rendered PDF pages, OCR, visual observations, metadata backfill, entity extraction, keyword indexing, and pgvector indexing.
- Hybrid retrieval across keyword, structured filters, pgvector text vectors, visual page vectors, and reranking.
- Controlled LangGraph QA workflow with query analysis, routing, retrieval, evidence review, query rewrite, answer writing, and safety review.
- Text and image QA endpoints with citations, retrieval trace, uncertainty, and `agent_trace`.
- Role-based authentication backed by PostgreSQL users and audit logs.
- RabbitMQ-backed task wakeup with PostgreSQL `ingest_tasks` as the source of truth.
- Local file storage for development and Aliyun OSS for deployment.

## Architecture

```text
Next.js static frontend
  -> /backend/* through Nginx
     -> FastAPI API
        -> Auth / Documents / QA / Chat Sessions / Retrieval Trace
        -> PostgreSQL tables
        -> PostgreSQL + pgvector embeddings
        -> Aliyun Bailian / PaddleOCR integrations

RabbitMQ
  -> worker
     -> ingestion pipeline
     -> keyword index + pgvector vector tables
```

Core packages:

| Area | Stack |
| --- | --- |
| API | FastAPI, SQLAlchemy Core, Alembic, Pydantic Settings |
| Worker | Python worker, RabbitMQ consumer, shared ingestion modules |
| Frontend | Next.js App Router, React, TypeScript, Tailwind CSS, assistant-ui |
| QA workflow | LangGraph, small controlled agents, langchain-core tool wrappers |
| Storage | PostgreSQL, pgvector, local files, Aliyun OSS |
| Models | Aliyun Bailian for LLM, embeddings, vision, rerank; PaddleOCR cloud API |

Graph RAG and Neo4j-related code remain in the repository for future experiments, but the current product path does not enable Graph RAG.

## Repository Layout

```text
backend/agromech_api/   FastAPI app, database models, QA, retrieval, ingestion modules
worker/agromech_worker/ Worker entrypoints and RabbitMQ consumer
frontend/               Next.js frontend
backend/alembic/        Database migrations
scripts/                Operational scripts
deploy/                 Production compose and Nginx examples
docs/                   Maintained product, API, database, deployment, and UX docs
```

## Requirements

- Python 3.11+
- Node.js compatible with Next.js 16
- PostgreSQL with the `pgvector` extension installed
- RabbitMQ
- Optional production integrations: Aliyun OSS, Aliyun Bailian, PaddleOCR cloud API

The local setup assumes PostgreSQL and RabbitMQ are provided by the sibling `../infrastructure` repository. You can also provide your own services and update `.env`.

## Quick Start

Create the Python environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
cp .env.example .env
```

Start shared dependencies when using `../infrastructure`:

```bash
cd ../infrastructure
docker compose --env-file env/.env -f compose/docker-compose.core.yml up -d postgres
docker compose --env-file env/.env -f compose/docker-compose.mq.yml up -d rabbitmq
cd ../AgroMech
```

Run migrations and create an administrator:

```bash
.venv/bin/python -m alembic upgrade head
.venv/bin/python scripts/create-user.py --username admin --role admin --display-name "Administrator"
```

Start the API:

```bash
.venv/bin/python -m uvicorn agromech_api.main:app --app-dir backend --host 0.0.0.0 --port 8000
```

Run one worker pass, or start the RabbitMQ consumer:

```bash
.venv/bin/python -m agromech_worker.main
.venv/bin/python -c "from agromech_worker.main import consume_forever; consume_forever()"
```

Start the frontend:

```bash
npm install --prefix frontend
npm run dev --prefix frontend
```

Health checks:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/dependencies
```

## pgvector Notes

AgroMech stores text and visual embeddings in PostgreSQL tables:

- `chunk_vector_embeddings`
- `visual_page_vector_embeddings`

Migrations execute `CREATE EXTENSION IF NOT EXISTS vector`, but the PostgreSQL image/container must already have pgvector installed. After replacing or clearing old vector data, rebuild indexed document vectors with:

```bash
.venv/bin/python scripts/rebuild-vector-index.py
```

For production Docker:

```bash
docker compose run --rm api python scripts/rebuild-vector-index.py
```

## Testing

Backend and worker tests currently pass:

```bash
.venv/bin/python -m pytest backend/tests worker/tests -q
```

Last verified on this branch:

```text
364 passed, 6 warnings
```

Lint currently reports no errors and one existing frontend warning:

```bash
scripts/lint.sh
```

Known frontend baseline issues on this branch:

- `npm run test --prefix frontend` fails 6 tests: `window is not defined` in `anonymous-chat-store.test.ts`, and one auth expectation mismatch in `agromech-chat.test.ts`.
- `npm run build --prefix frontend` fails during static prerender of `/` because assistant-ui expects `ThreadHistoryAdapter.withFormat`.
- `scripts/test-all.sh` therefore fails after the backend/worker test phase.

These frontend issues are outside the pgvector migration diff; no files under `frontend/` are changed by this branch.

## Deployment

Production deployment uses:

- static frontend files served by host Nginx;
- `/backend/*` reverse-proxied to FastAPI;
- Docker containers for API and worker;
- existing PostgreSQL and RabbitMQ services;
- pgvector-enabled PostgreSQL.

See [docs/deployment.md](docs/deployment.md) for the server layout, GitHub Actions workflow, Nginx example, migration command, and vector rebuild command.

## Documentation

- [docs/README.md](docs/README.md): documentation index and current system status
- [docs/prd.md](docs/prd.md): product scope and acceptance criteria
- [docs/tech-design.md](docs/tech-design.md): architecture, ingestion, retrieval, QA workflow, operations
- [docs/api-spec.md](docs/api-spec.md): runtime API specification
- [docs/database-design.md](docs/database-design.md): tables, states, indexes, pgvector storage
- [docs/deployment.md](docs/deployment.md): production deployment
- [docs/ux-spec.md](docs/ux-spec.md): frontend UX specification
- [docs/history.md](docs/history.md): decisions, baseline, and future work

## License

This repository does not currently include a `LICENSE` file. Until one is added, all rights are reserved.
