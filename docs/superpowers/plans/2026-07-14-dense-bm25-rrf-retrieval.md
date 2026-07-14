# Dense + BM25 + RRF 检索改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 AgroMech 当前的 token-overlap + 加权求和检索直接替换为“检索前 Query Rewrite + 结构化过滤 + Dense/BM25 + RRF + Rerank + Citation”流水线。

**Architecture:** 保留 PostgreSQL、pgvector、LangGraph、百炼 Rerank 和现有 Citation 契约；新增 `pg_search` BM25、共享过滤对象和独立 RRF 模块。Query Rewrite 在首次召回前执行，Dense 与 BM25 使用相同硬过滤，RRF 只融合两路排名，最终证据经 Rerank 后才进入 Citation 和答案生成。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL、pgvector、ParadeDB `pg_search` 0.24.2、Jieba、LangGraph、pytest、GitHub Actions。

## 全局约束

- 直接替换现有检索实现，不保留 `v1/v2` 双流水线，也不运行影子检索。
- `POST /qa/text` 的请求字段和响应字段保持兼容。
- Graph RAG 和 Neo4j 不进入主问答链路。
- 生产 PostgreSQL 必须同时提供 `vector` 与 `pg_search` 扩展。
- 默认参数固定为 `BM25_TOP_K=50`、`DENSE_TOP_K=50`、`RRF_K=60`、`FUSION_TOP_K=30`、`RERANK_TOP_K=30`、`FINAL_EVIDENCE_LIMIT=5`。
- Dense 与 BM25 的默认 RRF 权重均为 `1.0`。
- Query Rewrite 默认模型为 `qwen3.6-flash`，超时为 10 秒。
- Query Rewrite 必须完整保留机型、故障码、零件号、显式版本、语言和文档类型。
- 总检索轮次最多为两轮：一次 LLM 改写检索和一次确定性规则补充检索。
- 权限、可见性、所有者、文档状态及显式请求过滤必须在 Dense 和 BM25 的 Top K 截断前执行，并在融合后再次 fail-closed 校验。
- Citation 只能根据最终 Rerank 后的证据生成；无 Citation 时必须返回证据不足。
- SQLite 只使用标准 BM25 参考实现；生产行为必须由真实 PostgreSQL + `pg_search` 集成测试覆盖。
- 数据库迁移只新增扩展、索引和 Trace 字段，不删除旧字段；应用回滚使用上一镜像，不执行破坏性数据库降级。

---

## 文件结构

### 新建

- `backend/agromech_api/rag/retrieval/filters.py`：统一硬过滤对象及 SQLAlchemy 条件构建。
- `backend/agromech_api/rag/retrieval/fusion.py`：排名类型和标准 RRF。
- `backend/agromech_api/rag/retrieval/bm25.py`：BM25 接口、SQLite 参考实现和 PostgreSQL `pg_search` 实现。
- `backend/tests/test_retrieval_filters.py`：共享过滤与配置测试。
- `backend/tests/test_rrf_fusion.py`：RRF 公式、去重和稳定排序测试。
- `backend/tests/test_bm25_retrieval.py`：参考 BM25 与 PostgreSQL BM25 测试。
- `backend/alembic/versions/0013_add_pg_search_bm25.py`：`pg_search`、BM25/B-tree 索引与 Trace 字段迁移。
- `scripts/evaluate-retrieval.py`：在固定数据集上输出 Recall@20、nDCG@10 和检索 P95。

### 修改

- `pyproject.toml`：增加 Jieba 测试/本地参考实现依赖。
- `backend/agromech_api/core/config.py`：增加并校验 BM25、Dense、RRF、Fusion 和 Rewrite 配置。
- `backend/agromech_api/core/infrastructure.py`：检查 `pg_search` 扩展与 BM25 索引。
- `backend/agromech_api/api/health.py`：增加真正返回非 2xx 的 Readiness 路由。
- `backend/agromech_api/db/models.py`：增加 `retrieval_logs.query_rewrite` 与 `retrieval_logs.fusion`。
- `backend/agromech_api/rag/retrieval/query_rewrite.py`：增加百炼改写、实体保护和规则降级。
- `backend/agromech_api/rag/retrieval/indexing.py`：删除 token-overlap，向量检索接入共享过滤。
- `backend/agromech_api/rag/retrieval/hybrid.py`：使用 BM25 + Dense + RRF 直接替换旧通道融合。
- `backend/agromech_api/rag/agent/state.py`：保存 Query Rewrite 结果和检索轮次。
- `backend/agromech_api/rag/agent/agents/query_rewrite.py`：调用注入式 Query Rewrite 函数。
- `backend/agromech_api/rag/agent/agents/retrieval.py`：向检索工具传递原始查询和 Rewrite Trace。
- `backend/agromech_api/rag/agent/tools.py`、`backend/agromech_api/rag/langchain/adapters.py`：保持 Agent 到检索函数的 Rewrite 上下文。
- `backend/agromech_api/rag/agent/graph.py`：将 Rewrite 移到首次检索前，并保留一次规则补检索。
- `backend/agromech_api/rag/agent/controller.py`：注入 Rewrite 函数。
- `backend/agromech_api/qa/text.py`：构建 Rewrite Provider、共享过滤并保证 Citation 来自最终证据。
- `backend/agromech_api/rag/traces.py`：按角色返回 Rewrite/Fusion Trace。
- `backend/agromech_api/evaluation/runner.py`：增加 Recall@20 与 nDCG@10。
- `scripts/rebuild_vector_index.py`、`scripts/rebuild-vector-index.py`：明确重建 BM25 数据源与向量索引并输出结果。
- `.github/workflows/ci.yml`、`.github/workflows/deploy.yml`：增加真实 ParadeDB PostgreSQL 集成测试服务。
- `scripts/test-integration.sh`：加入 PostgreSQL BM25 集成测试。
- `.env.example`、`deploy/env.prod.example`：增加新配置。
- `README.md`、`docs/README.md`、`docs/prd.md`、`docs/api-spec.md`、`docs/tech-design.md`、`docs/database-design.md`、`docs/deployment.md`、`docs/history.md`：同步目标实现、接口说明与运行手册。
- `backend/tests/test_infrastructure_config.py`、`backend/tests/test_query_rewrite.py`、`backend/tests/test_migrations.py`、`backend/tests/test_dependency_health.py`、`backend/tests/test_hybrid_retrieval.py`、`backend/tests/test_search_indexing.py`、`backend/tests/test_agent_controller.py`、`backend/tests/test_langchain_adapters.py`、`backend/tests/test_text_qa.py`、`backend/tests/test_retrieval_trace.py`、`backend/tests/test_evaluation_runner.py`、`backend/tests/test_rebuild_vector_index.py`、`backend/tests/test_docs_sync.py`：覆盖配置、迁移、检索、Agent、Citation、Trace、评估和文档同步。

---

## 实施前置：记录旧检索基线

必须在 Task 1 修改任何运行代码之前执行。当前数据库必须已导入 `curated-mvp` 评估集。

Run:

```bash
.venv/bin/python - <<'PY' > /tmp/agromech-retrieval-baseline.json
from __future__ import annotations

import json
import math
import statistics
import time

from agromech_api.core.database import get_engine
from agromech_api.evaluation.runner import load_evaluation_questions
from agromech_api.rag.retrieval.hybrid import hybrid_retrieve


def relevant(candidate, expected):
    if expected.get("chunk_id"):
        return str(candidate.get("chunk_id")) == str(expected["chunk_id"])
    return str(candidate.get("document_id")) == str(expected["document_id"])


def recall(candidates, expected, k):
    if not expected:
        return 0.0
    return sum(any(relevant(candidate, source) for candidate in candidates[:k]) for source in expected) / len(expected)


def ndcg(candidates, expected, k):
    remaining = list(expected)
    gains = []
    for candidate in candidates[:k]:
        match = next((source for source in remaining if relevant(candidate, source)), None)
        gains.append(1.0 if match else 0.0)
        if match:
            remaining.remove(match)
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected), k)))
    return dcg / ideal if ideal else 0.0


engine = get_engine()
questions = load_evaluation_questions(engine, dataset_version="curated-mvp")
if not questions:
    raise SystemExit("curated-mvp contains no evaluation questions")
recalls = []
ndcgs = []
durations = []
for question in questions:
    started = time.perf_counter()
    result = hybrid_retrieve(engine, question.question, limit=20)
    durations.append((time.perf_counter() - started) * 1000)
    if question.expected_sources:
        recalls.append(recall(result.get("candidates", []), question.expected_sources, 20))
        ndcgs.append(ndcg(result.get("candidates", []), question.expected_sources, 10))
ordered = sorted(durations)
p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
print(json.dumps({
    "dataset": "curated-mvp",
    "question_count": len(questions),
    "recall_at_20": statistics.fmean(recalls) if recalls else 0.0,
    "ndcg_at_10": statistics.fmean(ndcgs) if ndcgs else 0.0,
    "retrieval_p95_ms": ordered[p95_index],
}, ensure_ascii=False, indent=2))
PY
```

Expected: `/tmp/agromech-retrieval-baseline.json` contains non-negative `recall_at_20`, `ndcg_at_10`, and `retrieval_p95_ms` values. Copy this file into the release evidence store used by the team; do not commit environment-specific measurements to Git.

---

### Task 1: 增加检索配置与共享硬过滤契约

**Files:**
- Create: `backend/agromech_api/rag/retrieval/filters.py`
- Create: `backend/tests/test_retrieval_filters.py`
- Modify: `backend/agromech_api/core/config.py:103-151`
- Modify: `backend/tests/test_infrastructure_config.py:118-160`

**Interfaces:**
- Produces: `RetrievalFilters`、`build_retrieval_filters()`、`document_filter_conditions()`、`chunk_filter_conditions()`。
- Consumes: `documents`、`chunk_entity_links`、`DocumentStatus` 和 API `filters` 字典。
- Later tasks must pass the same immutable `RetrievalFilters` instance to Dense and BM25。

- [ ] **Step 1: 写配置和过滤契约的失败测试**

Create `backend/tests/test_retrieval_filters.py`:

```python
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.core.config import Settings
from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import documents, metadata
from agromech_api.rag.retrieval.filters import (
    build_retrieval_filters,
    document_filter_conditions,
)


def test_retrieval_settings_defaults_and_ordering() -> None:
    settings = Settings(_env_file=None)

    assert settings.bm25_top_k == 50
    assert settings.dense_top_k == 50
    assert settings.rrf_k == 60
    assert settings.rrf_dense_weight == 1.0
    assert settings.rrf_bm25_weight == 1.0
    assert settings.fusion_top_k == 30
    assert settings.query_rewrite_model == "qwen3.6-flash"
    assert settings.query_rewrite_timeout_seconds == 10.0
    assert settings.optional_retrieval_channel_list == ["dense", "bm25", "vision", "rerank"]

    with pytest.raises(ValueError, match="FINAL_EVIDENCE_LIMIT must be <= FUSION_TOP_K"):
        Settings(_env_file=None, final_evidence_limit=31, fusion_top_k=30, rerank_top_k=40)

    with pytest.raises(ValueError, match="FINAL_EVIDENCE_LIMIT must be <= RERANK_TOP_K"):
        Settings(_env_file=None, final_evidence_limit=6, rerank_top_k=5)

    with pytest.raises(ValueError, match="RERANK_TOP_K must be <= FUSION_TOP_K"):
        Settings(_env_file=None, rerank_top_k=31, fusion_top_k=30)

    with pytest.raises(ValueError, match="RRF weights must not both be zero"):
        Settings(_env_file=None, rrf_dense_weight=0, rrf_bm25_weight=0)


def test_explicit_filters_are_applied_before_retrieval(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'filters.db'}")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [
                {
                    "id": "public-m7040",
                    "title": "M7040 Manual",
                    "original_file_name": "m7040.txt",
                    "file_hash": "hash-1",
                    "file_size_bytes": 1,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///m7040.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "status": DocumentStatus.INDEXED.value,
                    "visibility": "public",
                    "created_by_role": "admin",
                },
                {
                    "id": "public-l3901",
                    "title": "L3901 Manual",
                    "original_file_name": "l3901.txt",
                    "file_hash": "hash-2",
                    "file_size_bytes": 1,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///l3901.txt",
                    "brand": "Kubota",
                    "model": "L3901",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "status": DocumentStatus.INDEXED.value,
                    "visibility": "public",
                    "created_by_role": "admin",
                },
                {
                    "id": "deleted-m7040",
                    "title": "Deleted M7040 Manual",
                    "original_file_name": "deleted-m7040.txt",
                    "file_hash": "hash-3",
                    "file_size_bytes": 1,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///deleted-m7040.txt",
                    "brand": "Kubota",
                    "model": "M7040",
                    "document_type": "repair_manual",
                    "language": "zh-CN",
                    "status": DocumentStatus.INDEXED.value,
                    "visibility": "public",
                    "created_by_role": "admin",
                    "deleted_at": datetime.now(UTC),
                },
            ],
        )

    filters = build_retrieval_filters(
        request_filters={"brand": "Kubota", "model": "M7040", "language": "zh-CN"},
        viewer_user_id=None,
    )
    with engine.connect() as connection:
        ids = connection.execute(
            select(documents.c.id).where(*document_filter_conditions(filters))
        ).scalars().all()

    assert ids == ["public-m7040"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_retrieval_filters.py backend/tests/test_infrastructure_config.py -q`

Expected: FAIL，首先出现 `ModuleNotFoundError: agromech_api.rag.retrieval.filters`。

- [ ] **Step 3: 实现配置与共享过滤对象**

Add to `backend/agromech_api/core/config.py`:

```python
    # Hybrid retrieval
    bm25_top_k: int = 50
    dense_top_k: int = 50
    rrf_k: int = 60
    rrf_dense_weight: float = 1.0
    rrf_bm25_weight: float = 1.0
    fusion_top_k: int = 30
    query_rewrite_enabled: bool = True
    query_rewrite_model: str = "qwen3.6-flash"
    query_rewrite_timeout_seconds: float = 10.0
```

Change the existing optional channel default to:

```python
    optional_retrieval_channels: str = "dense,bm25,vision,rerank"
```

Extend `validate_backend_modes()` with:

```python
        positive_values = {
            "BM25_TOP_K": self.bm25_top_k,
            "DENSE_TOP_K": self.dense_top_k,
            "RRF_K": self.rrf_k,
            "FUSION_TOP_K": self.fusion_top_k,
            "RERANK_TOP_K": self.rerank_top_k,
            "FINAL_EVIDENCE_LIMIT": self.final_evidence_limit,
        }
        for name, value in positive_values.items():
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.query_rewrite_timeout_seconds <= 0:
            raise ValueError("QUERY_REWRITE_TIMEOUT_SECONDS must be positive")
        if self.rrf_dense_weight < 0 or self.rrf_bm25_weight < 0:
            raise ValueError("RRF weights must be non-negative")
        if self.rrf_dense_weight == 0 and self.rrf_bm25_weight == 0:
            raise ValueError("RRF weights must not both be zero")
        if self.final_evidence_limit > self.fusion_top_k:
            raise ValueError("FINAL_EVIDENCE_LIMIT must be <= FUSION_TOP_K")
        if self.final_evidence_limit > self.rerank_top_k:
            raise ValueError("FINAL_EVIDENCE_LIMIT must be <= RERANK_TOP_K")
        if self.rerank_top_k > self.fusion_top_k:
            raise ValueError("RERANK_TOP_K must be <= FUSION_TOP_K")
        if self.fusion_top_k > self.bm25_top_k + self.dense_top_k:
            raise ValueError("FUSION_TOP_K must be <= BM25_TOP_K + DENSE_TOP_K")
```

Create `backend/agromech_api/rag/retrieval/filters.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import exists, or_, select

from agromech_api.db.enums import DocumentStatus
from agromech_api.db.models import chunk_entity_links, documents
from agromech_api.domain.entities import normalize


@dataclass(frozen=True)
class RetrievalFilters:
    viewer_user_id: str | None
    brand: str | None = None
    model: str | None = None
    document_type: str | None = None
    language: str | None = None
    document_version: str | None = None
    subsystem: str | None = None

    def as_trace(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "brand": self.brand,
                "model": self.model,
                "document_type": self.document_type,
                "language": self.language,
                "document_version": self.document_version,
                "subsystem": self.subsystem,
            }.items()
            if value is not None
        }


def build_retrieval_filters(
    *, request_filters: dict[str, str | None], viewer_user_id: str | None
) -> RetrievalFilters:
    values = {key: normalized_filter(request_filters.get(key)) for key in RetrievalFilters.__dataclass_fields__ if key != "viewer_user_id"}
    if values["subsystem"] is not None:
        values["subsystem"] = normalize(values["subsystem"])
    return RetrievalFilters(viewer_user_id=viewer_user_id, **values)


def normalized_filter(value: str | None) -> str | None:
    return value.strip() if value and value.strip() else None


def document_filter_conditions(filters: RetrievalFilters) -> list[object]:
    visibility = documents.c.visibility == "public"
    if filters.viewer_user_id is not None:
        visibility = or_(visibility, documents.c.owner_user_id == filters.viewer_user_id)
    conditions: list[object] = [
        documents.c.status == DocumentStatus.INDEXED.value,
        documents.c.deleted_at.is_(None),
        visibility,
    ]
    for field in ("brand", "model", "document_type", "language", "document_version"):
        value = getattr(filters, field)
        if value is not None:
            conditions.append(getattr(documents.c, field) == value)
    return conditions


def chunk_filter_conditions(chunk_id_column, filters: RetrievalFilters) -> list[object]:
    if filters.subsystem is None:
        return []
    return [
        exists(
            select(chunk_entity_links.c.id).where(
                chunk_entity_links.c.chunk_id == chunk_id_column,
                chunk_entity_links.c.entity_type == "system",
                chunk_entity_links.c.normalized_value == normalize(filters.subsystem),
            )
        )
    ]
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `.venv/bin/python -m pytest backend/tests/test_retrieval_filters.py backend/tests/test_infrastructure_config.py -q`

Expected: PASS。

- [ ] **Step 5: 提交任务**

```bash
git add backend/agromech_api/core/config.py backend/agromech_api/rag/retrieval/filters.py backend/tests/test_retrieval_filters.py backend/tests/test_infrastructure_config.py
git commit -m "feat: add retrieval settings and shared filters"
```

---

### Task 2: 实现检索前 Query Rewrite 与实体保护

**Files:**
- Modify: `backend/agromech_api/rag/retrieval/query_rewrite.py`
- Modify: `backend/tests/test_query_rewrite.py`
- Modify later: `backend/agromech_api/qa/text.py`

**Interfaces:**
- Produces: `QueryRewriteResult`、`QueryRewriteProvider`、`BailianQueryRewriteProvider`、`rewrite_query()`、`build_query_rewrite_provider()`。
- Consumes: 原始问题、`ParsedQuery`、显式请求过滤和 `Settings`。
- `rewrite_query()` must return a complete Trace-ready result even when it falls back。

- [ ] **Step 1: 写 Provider、实体保护和降级测试**

Append to `backend/tests/test_query_rewrite.py`:

```python
from agromech_api.core.config import Settings
from agromech_api.rag.retrieval.query_rewrite import (
    BailianQueryRewriteProvider,
    rewrite_query,
)
from agromech_api.rag.retrieval.query_understanding import parse_query


def rewrite_settings() -> Settings:
    return Settings(
        _env_file=None,
        model_provider="bailian",
        embedding_provider="local",
        bailian_api_key="test-key",
        bailian_base_url="https://bailian.example",
    )


def test_llm_rewrite_preserves_protected_identifiers() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"M7040 E01 液压泵 hydraulic pump 检查"}'}}]
        },
    )

    result = rewrite_query(
        question="M7040 的 E01 液压泵怎么检查？",
        parsed=parse_query("M7040 的 E01 液压泵怎么检查？"),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    assert result.query == "M7040 E01 液压泵 hydraulic pump 检查"
    assert result.original_query == "M7040 的 E01 液压泵怎么检查？"
    assert result.fallback is False
    assert result.protected_identifiers == ["M7040", "E01"]


def test_llm_rewrite_losing_model_uses_rule_fallback() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"E01 hydraulic pump 检查"}'}}]
        },
    )

    result = rewrite_query(
        question="M7040 的 E01 液压泵怎么检查？",
        parsed=parse_query("M7040 的 E01 液压泵怎么检查？"),
        request_filters={},
        provider=provider,
        supplemental=False,
    )

    assert result.fallback is True
    assert result.reason == "protected_identifier_missing:M7040"
    assert "M7040" in result.query
    assert "hydraulic pump" in result.query


def test_rewrite_protects_part_number_version_language_and_document_type() -> None:
    provider = BailianQueryRewriteProvider(
        rewrite_settings(),
        transport=lambda _request, _timeout: {
            "choices": [{"message": {"content": '{"query":"RE-12345 repair_manual zh-CN 2024 查询"}'}}]
        },
    )
    question = "RE-12345 repair_manual zh-CN 2024 查询"
    result = rewrite_query(
        question=question,
        parsed=parse_query(question),
        request_filters={},
        provider=provider,
        supplemental=False,
    )
    assert result.fallback is False
    assert set(result.protected_identifiers) == {"RE-12345", "2024", "zh-CN", "repair_manual"}


def test_supplemental_rewrite_never_calls_provider() -> None:
    calls = []

    class ExplodingProvider:
        provider = "test"
        model = "test"

        def rewrite(self, question: str, protected_identifiers: list[str]) -> str:
            calls.append(question)
            raise AssertionError("provider must not be called")

    result = rewrite_query(
        question="液压泵异响怎么检查？",
        parsed=parse_query("液压泵异响怎么检查？"),
        request_filters={},
        provider=ExplodingProvider(),
        supplemental=True,
    )

    assert calls == []
    assert result.fallback is True
    assert "hydraulic pump" in result.query
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_query_rewrite.py -q`

Expected: FAIL，缺少 `BailianQueryRewriteProvider` 和 `rewrite_query`。

- [ ] **Step 3: 实现 Query Rewrite 契约与百炼适配器**

Keep `DOMAIN_SYNONYMS` and add to `backend/agromech_api/rag/retrieval/query_rewrite.py`:

```python
import json
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from agromech_api.core.config import Settings
from agromech_api.rag.retrieval.query_understanding import ParsedQuery


RewriteTransport = Callable[[urllib.request.Request, float], dict[str, object]]


@dataclass(frozen=True)
class QueryRewriteResult:
    original_query: str
    query: str
    provider: str
    model: str | None
    fallback: bool
    reason: str
    protected_identifiers: list[str]
    duration_ms: float

    def to_trace(self) -> dict[str, object]:
        return asdict(self)


class QueryRewriteProvider(Protocol):
    provider: str
    model: str

    def rewrite(self, question: str, protected_identifiers: list[str]) -> str: ...


class BailianQueryRewriteProvider:
    provider = "bailian"

    def __init__(self, settings: Settings, *, transport: RewriteTransport | None = None) -> None:
        self.model = settings.query_rewrite_model
        self.timeout = settings.query_rewrite_timeout_seconds
        self._api_key = settings.bailian_api_key
        self._base_url = settings.bailian_base_url.rstrip("/")
        self._transport = transport or self._default_transport

    def rewrite(self, question: str, protected_identifiers: list[str]) -> str:
        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": "Rewrite one retrieval query as JSON {query:string}. Preserve every protected identifier exactly. Do not answer the question."},
                {"role": "user", "content": json.dumps({"question": question, "protected_identifiers": protected_identifiers}, ensure_ascii=False)},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        body = self._transport(request, self.timeout)
        content = body["choices"][0]["message"]["content"]
        query = json.loads(str(content))["query"]
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query rewrite response missing query")
        return query.strip()

    def _default_transport(self, request: urllib.request.Request, timeout: float) -> dict[str, object]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def protected_identifiers(parsed: ParsedQuery, request_filters: dict[str, str | None]) -> list[str]:
    values = [
        *[str(value) for value in parsed.entities.get("model", [])],
        *[str(value) for value in parsed.entities.get("fault_code", [])],
        *[str(value) for value in parsed.entities.get("part_number", [])],
    ]
    for key in ("model", "document_version", "language", "document_type"):
        value = request_filters.get(key) or parsed.filters.get(key)
        if value:
            values.append(str(value).strip())
    return list(dict.fromkeys(value for value in values if value))


def rewrite_query(*, question: str, parsed: ParsedQuery, request_filters: dict[str, str | None], provider: QueryRewriteProvider | None, supplemental: bool) -> QueryRewriteResult:
    started = time.perf_counter()
    protected = protected_identifiers(parsed, request_filters)
    if supplemental or provider is None:
        fallback = rewrite_query_for_evidence(question=question, filters=request_filters, missing=[])
        return QueryRewriteResult(question, fallback["query"], "rule", None, True, "supplemental" if supplemental else "provider_unavailable", protected, (time.perf_counter() - started) * 1000)
    try:
        rewritten = provider.rewrite(question, protected)
        missing = next((value for value in protected if value.lower() not in rewritten.lower()), None)
        if missing:
            raise ValueError(f"protected_identifier_missing:{missing}")
        return QueryRewriteResult(question, rewritten, provider.provider, provider.model, False, "model_rewrite", protected, (time.perf_counter() - started) * 1000)
    except Exception as exc:  # noqa: BLE001 - rewrite degrades to deterministic rules
        fallback = rewrite_query_for_evidence(question=question, filters=request_filters, missing=[])
        reason = str(exc) if str(exc).startswith("protected_identifier_missing:") else "provider_error"
        return QueryRewriteResult(question, fallback["query"], "rule", None, True, reason, protected, (time.perf_counter() - started) * 1000)


def build_query_rewrite_provider(settings: Settings) -> QueryRewriteProvider | None:
    if settings.query_rewrite_enabled and settings.model_provider == "bailian":
        return BailianQueryRewriteProvider(settings)
    return None
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `.venv/bin/python -m pytest backend/tests/test_query_rewrite.py -q`

Expected: PASS。

- [ ] **Step 5: 提交任务**

```bash
git add backend/agromech_api/rag/retrieval/query_rewrite.py backend/tests/test_query_rewrite.py
git commit -m "feat: add guarded query rewrite"
```

---

### Task 3: 实现标准 RRF 融合

**Files:**
- Create: `backend/agromech_api/rag/retrieval/fusion.py`
- Create: `backend/tests/test_rrf_fusion.py`

**Interfaces:**
- Produces: `RankedHit`、`FusedHit`、`rrf_fuse()`。
- Consumes: `dict[channel, list[RankedHit]]`，每个通道排名从 1 开始。
- Later hybrid retrieval must use `FusedHit.rrf_score` as the pre-rerank score。

- [ ] **Step 1: 写 RRF 公式、去重和稳定排序测试**

Create `backend/tests/test_rrf_fusion.py`:

```python
from agromech_api.rag.retrieval.fusion import RankedHit, rrf_fuse


def hit(chunk_id: str, rank: int, score: float) -> RankedHit:
    return RankedHit(chunk_id=chunk_id, rank=rank, score=score)


def test_rrf_fuses_ranks_without_using_raw_score_scale() -> None:
    fused, trace = rrf_fuse(
        {
            "dense": [hit("a", 1, 0.91), hit("b", 2, 0.90)],
            "bm25": [hit("b", 1, 1000.0), hit("a", 2, 1.0)],
        },
        rrf_k=60,
        weights={"dense": 1.0, "bm25": 1.0},
        limit=10,
    )

    assert [item.chunk_id for item in fused] == ["a", "b"]
    assert fused[0].rrf_score == fused[1].rrf_score
    assert fused[0].channel_ranks == {"dense": 1, "bm25": 2}
    assert trace["rrf_k"] == 60


def test_rrf_supports_one_channel_and_deduplicates_chunk() -> None:
    fused, _trace = rrf_fuse(
        {"dense": [hit("a", 1, 0.9), hit("a", 2, 0.8), hit("b", 3, 0.7)]},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )

    assert [item.chunk_id for item in fused] == ["a", "b"]
    assert fused[0].channel_ranks == {"dense": 1}


def test_rrf_ties_use_best_rank_then_chunk_id() -> None:
    fused, _trace = rrf_fuse(
        {"dense": [hit("b", 1, 0.9), hit("a", 1, 0.9)]},
        rrf_k=60,
        weights={"dense": 1.0},
        limit=10,
    )

    assert [item.chunk_id for item in fused] == ["a", "b"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_rrf_fusion.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现排名类型和 RRF**

Create `backend/agromech_api/rag/retrieval/fusion.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RankedHit:
    chunk_id: str
    rank: int
    score: float
    vector_ref: str | None = None
    embedding_id: str | None = None


@dataclass
class FusedHit:
    chunk_id: str
    rrf_score: float = 0.0
    channel_ranks: dict[str, int] = field(default_factory=dict)
    channel_scores: dict[str, float] = field(default_factory=dict)
    vector_ref: str | None = None
    embedding_id: str | None = None


def rrf_fuse(
    channel_hits: dict[str, list[RankedHit]],
    *,
    rrf_k: int,
    weights: dict[str, float],
    limit: int,
) -> tuple[list[FusedHit], dict[str, object]]:
    fused: dict[str, FusedHit] = {}
    for channel, hits in channel_hits.items():
        seen: set[str] = set()
        for hit in hits:
            if hit.chunk_id in seen:
                continue
            seen.add(hit.chunk_id)
            item = fused.setdefault(hit.chunk_id, FusedHit(chunk_id=hit.chunk_id))
            item.channel_ranks[channel] = hit.rank
            item.channel_scores[channel] = hit.score
            item.rrf_score += weights.get(channel, 0.0) / (rrf_k + hit.rank)
            item.vector_ref = item.vector_ref or hit.vector_ref
            item.embedding_id = item.embedding_id or hit.embedding_id
    ranked = sorted(
        fused.values(),
        key=lambda item: (-item.rrf_score, min(item.channel_ranks.values()), item.chunk_id),
    )[:limit]
    trace = {
        "rrf_k": rrf_k,
        "weights": dict(weights),
        "channel_counts": {channel: len(hits) for channel, hits in channel_hits.items()},
        "items": [
            {
                "chunk_id": item.chunk_id,
                "channel_ranks": dict(item.channel_ranks),
                "channel_scores": dict(item.channel_scores),
                "rrf_score": round(item.rrf_score, 8),
            }
            for item in ranked
        ],
    }
    return ranked, trace
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `.venv/bin/python -m pytest backend/tests/test_rrf_fusion.py -q`

Expected: PASS。

- [ ] **Step 5: 提交任务**

```bash
git add backend/agromech_api/rag/retrieval/fusion.py backend/tests/test_rrf_fusion.py
git commit -m "feat: add reciprocal rank fusion"
```

---

### Task 4: 实现 BM25 接口、SQLite 参考算法和 PostgreSQL 查询

**Files:**
- Create: `backend/agromech_api/rag/retrieval/bm25.py`
- Create: `backend/tests/test_bm25_retrieval.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `Bm25Retriever`、`ReferenceBm25Retriever`、`PostgresBm25Retriever`、`build_bm25_retriever()`。
- Consumes: `RetrievalFilters` and returns `list[RankedHit]`。
- PostgreSQL query must use `search_text ||| :query` and `pdb.score(id)`; SQLite must use standard BM25 with `k1=1.2`, `b=0.75`。

- [ ] **Step 1: 写参考 BM25 与过滤测试**

Create `backend/tests/test_bm25_retrieval.py`:

```python
from sqlalchemy import create_engine, delete, insert

from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import chunk_search_index, document_chunks, documents, metadata
from agromech_api.rag.retrieval.bm25 import ReferenceBm25Retriever
from agromech_api.rag.retrieval.filters import build_retrieval_filters


def seed_bm25_rows(engine) -> None:
    with engine.begin() as connection:
        connection.execute(delete(chunk_search_index).where(chunk_search_index.c.document_id.in_(["doc-m7040", "doc-l3901"])))
        connection.execute(delete(document_chunks).where(document_chunks.c.document_id.in_(["doc-m7040", "doc-l3901"])))
        connection.execute(delete(documents).where(documents.c.id.in_(["doc-m7040", "doc-l3901"])))
        connection.execute(insert(documents), [
            {"id": "doc-m7040", "title": "M7040", "original_file_name": "m.txt", "file_hash": "m", "file_size_bytes": 1, "mime_type": "text/plain", "storage_uri": "file:///m", "brand": "Kubota", "model": "M7040", "document_type": "repair_manual", "language": "zh-CN", "status": DocumentStatus.INDEXED.value, "visibility": "public", "created_by_role": "admin"},
            {"id": "doc-l3901", "title": "L3901", "original_file_name": "l.txt", "file_hash": "l", "file_size_bytes": 1, "mime_type": "text/plain", "storage_uri": "file:///l", "brand": "Kubota", "model": "L3901", "document_type": "repair_manual", "language": "zh-CN", "status": DocumentStatus.INDEXED.value, "visibility": "public", "created_by_role": "admin"},
        ])
        connection.execute(insert(document_chunks), [
            {"id": "chunk-m", "document_id": "doc-m7040", "chunk_type": ChunkType.TEXT.value, "content": "M7040 E01 液压泵 hydraulic pump 检查", "source_locator": {"type": "text", "line_start": 1, "line_end": 1}},
            {"id": "chunk-l", "document_id": "doc-l3901", "chunk_type": ChunkType.TEXT.value, "content": "L3901 电气传感器 electrical sensor 检查", "source_locator": {"type": "text", "line_start": 1, "line_end": 1}},
        ])
        connection.execute(insert(chunk_search_index), [
            {"id": "idx-m", "chunk_id": "chunk-m", "document_id": "doc-m7040", "chunk_type": ChunkType.TEXT.value, "search_text": "M7040 E01 液压泵 hydraulic pump 检查", "embedding_version": "v1", "chunk_profile": "chunk-v1", "embedding_dimension": 1024},
            {"id": "idx-l", "chunk_id": "chunk-l", "document_id": "doc-l3901", "chunk_type": ChunkType.TEXT.value, "search_text": "L3901 电气传感器 electrical sensor 检查", "embedding_version": "v1", "chunk_profile": "chunk-v1", "embedding_dimension": 1024},
        ])


def test_reference_bm25_ranks_relevant_chinese_and_code_tokens(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'bm25.db'}")
    metadata.create_all(engine)
    seed_bm25_rows(engine)

    hits = ReferenceBm25Retriever().search(
        engine,
        "M7040 E01 液压泵",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        limit=10,
    )

    assert [hit.chunk_id for hit in hits] == ["chunk-m"]
    assert hits[0].rank == 1
    assert hits[0].score > 0


def test_reference_bm25_applies_explicit_model_before_limit(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'bm25-filter.db'}")
    metadata.create_all(engine)
    seed_bm25_rows(engine)

    hits = ReferenceBm25Retriever().search(
        engine,
        "检查",
        filters=build_retrieval_filters(request_filters={"model": "M7040"}, viewer_user_id=None),
        limit=1,
    )

    assert [hit.chunk_id for hit in hits] == ["chunk-m"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_bm25_retrieval.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 增加 Jieba 依赖并实现 BM25**

Add to `pyproject.toml` dependencies:

```toml
  "jieba>=0.42.1",
```

Create `backend/agromech_api/rag/retrieval/bm25.py` with these public methods and formulas:

```python
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Protocol

import jieba
from sqlalchemy import Engine, select, text

from agromech_api.db.models import chunk_search_index, documents
from agromech_api.rag.retrieval.filters import (
    RetrievalFilters,
    chunk_filter_conditions,
    document_filter_conditions,
)
from agromech_api.rag.retrieval.fusion import RankedHit


CODE_OR_CJK_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*|[\u4e00-\u9fff]+")


class Bm25Retriever(Protocol):
    def search(self, engine: Engine, query: str, *, filters: RetrievalFilters, limit: int) -> list[RankedHit]: ...


def bm25_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for part in CODE_OR_CJK_RE.findall(value or ""):
        if re.fullmatch(r"[\u4e00-\u9fff]+", part):
            tokens.extend(token.strip().lower() for token in jieba.cut_for_search(part) if token.strip())
        else:
            tokens.append(part.lower())
    return tokens


class ReferenceBm25Retriever:
    def __init__(self, *, k1: float = 1.2, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b

    def search(self, engine: Engine, query: str, *, filters: RetrievalFilters, limit: int) -> list[RankedHit]:
        statement = (
            select(chunk_search_index.c.chunk_id, chunk_search_index.c.search_text)
            .select_from(chunk_search_index.join(documents, chunk_search_index.c.document_id == documents.c.id))
            .where(*document_filter_conditions(filters))
            .where(*chunk_filter_conditions(chunk_search_index.c.chunk_id, filters))
        )
        with engine.connect() as connection:
            rows = connection.execute(statement).mappings().all()
        documents_tokens = [(row["chunk_id"], bm25_tokens(row["search_text"])) for row in rows]
        query_tokens = list(dict.fromkeys(bm25_tokens(query)))
        average_length = sum(len(tokens) for _, tokens in documents_tokens) / max(1, len(documents_tokens))
        document_frequency = {term: sum(1 for _, tokens in documents_tokens if term in set(tokens)) for term in query_tokens}
        scored: list[tuple[str, float]] = []
        for chunk_id, tokens in documents_tokens:
            frequencies = Counter(tokens)
            score = 0.0
            for term in query_tokens:
                frequency = frequencies[term]
                if frequency == 0:
                    continue
                idf = math.log(1 + (len(documents_tokens) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
                denominator = frequency + self.k1 * (1 - self.b + self.b * len(tokens) / max(1.0, average_length))
                score += idf * frequency * (self.k1 + 1) / denominator
            if score > 0:
                scored.append((str(chunk_id), score))
        ranked = sorted(scored, key=lambda item: (-item[1], item[0]))[:limit]
        return [RankedHit(chunk_id=chunk_id, rank=index, score=score) for index, (chunk_id, score) in enumerate(ranked, start=1)]


class PostgresBm25Retriever:
    def search(self, engine: Engine, query: str, *, filters: RetrievalFilters, limit: int) -> list[RankedHit]:
        statement = text("""
            SELECT csi.chunk_id, pdb.score(csi.id) AS score
            FROM chunk_search_index AS csi
            JOIN documents AS d ON d.id = csi.document_id
            WHERE csi.search_text ||| :query
              AND d.status = 'indexed'
              AND d.deleted_at IS NULL
              AND (d.visibility = 'public' OR (:viewer_user_id IS NOT NULL AND d.owner_user_id = :viewer_user_id))
              AND (:brand IS NULL OR d.brand = :brand)
              AND (:model IS NULL OR d.model = :model)
              AND (:document_type IS NULL OR d.document_type = :document_type)
              AND (:language IS NULL OR d.language = :language)
              AND (:document_version IS NULL OR d.document_version = :document_version)
              AND (:subsystem IS NULL OR EXISTS (
                    SELECT 1 FROM chunk_entity_links AS cel
                    WHERE cel.chunk_id = csi.chunk_id
                      AND cel.entity_type = 'system'
                      AND cel.normalized_value = :subsystem
              ))
            ORDER BY pdb.score(csi.id) DESC, csi.id ASC
            LIMIT :limit
        """)
        params = {**filters.__dict__, "query": query, "limit": limit}
        with engine.connect() as connection:
            rows = connection.execute(statement, params).mappings().all()
        return [RankedHit(chunk_id=str(row["chunk_id"]), rank=index, score=float(row["score"])) for index, row in enumerate(rows, start=1)]


def build_bm25_retriever(engine: Engine) -> Bm25Retriever:
    return PostgresBm25Retriever() if engine.dialect.name == "postgresql" else ReferenceBm25Retriever()
```

- [ ] **Step 4: 运行测试并确认通过**

Run: `.venv/bin/python -m pytest backend/tests/test_bm25_retrieval.py -q`

Expected: PASS。

- [ ] **Step 5: 提交任务**

```bash
git add pyproject.toml backend/agromech_api/rag/retrieval/bm25.py backend/tests/test_bm25_retrieval.py
git commit -m "feat: add bm25 retrieval backends"
```

---

### Task 5: 增加 pg_search 迁移、真实 PostgreSQL 测试与 Readiness

**Files:**
- Create: `backend/alembic/versions/0013_add_pg_search_bm25.py`
- Modify: `backend/agromech_api/db/models.py:304-318`
- Modify: `backend/tests/test_migrations.py`
- Modify: `backend/tests/test_bm25_retrieval.py`
- Modify: `backend/agromech_api/core/infrastructure.py:142-175`
- Modify: `backend/agromech_api/api/health.py:23-30`
- Modify: `backend/tests/test_infrastructure_config.py`
- Modify: `backend/tests/test_dependency_health.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/deploy.yml`
- Modify: `scripts/test-integration.sh`

**Interfaces:**
- Produces: migration revision `0013_pg_search_bm25`、Trace JSON columns、`check_pg_search_extension()` and `/health/ready`。
- Consumes: ParadeDB Docker image `paradedb/paradedb:0.24.2` and `AGROMECH_TEST_POSTGRES_URL`。
- BM25 index name: `ix_chunk_search_index_bm25`，key field: `id`，text tokenizer: `pdb.jieba`。
- B-tree indexes: `ix_documents_retrieval_state(status, deleted_at, visibility, owner_user_id)` and `ix_documents_retrieval_metadata(document_type, language, document_version)`；现有 `ix_documents_brand_model` 继续服务品牌/机型过滤。

- [ ] **Step 1: 写迁移和 Readiness 失败测试**

Extend the existing `test_alembic_migration_can_run_repeatedly()` in `backend/tests/test_migrations.py` after `retrieval_log_columns` is computed:

```python
    assert {"query_rewrite", "fusion"}.issubset(retrieval_log_columns)
    document_indexes = {index["name"] for index in inspector.get_indexes("documents")}
    assert "ix_documents_retrieval_state" in document_indexes
    assert "ix_documents_retrieval_metadata" in document_indexes
```

Append to `backend/tests/test_infrastructure_config.py`:

```python
def test_pg_search_extension_health_check_reports_bm25_index() -> None:
    from agromech_api.core.infrastructure import check_pg_search_extension

    class FakeResult:
        def mappings(self):
            return self

        def one(self):
            return {"extension": True, "index": True}

    class FakeConnection:
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def execute(self, statement):
            assert "pg_search" in str(statement)
            assert "ix_chunk_search_index_bm25" in str(statement)
            return FakeResult()

    class FakeEngine:
        def connect(self): return FakeConnection()

    check = check_pg_search_extension(FakeEngine())
    assert check.status == "ok"
    assert check.name == "pg_search"
```

Append to `backend/tests/test_dependency_health.py`:

```python
def test_readiness_returns_503_when_required_search_dependency_is_missing() -> None:
    client = TestClient(create_app(dependency_checker=lambda: [
        DependencyCheck("postgres", "ok", "localhost:5432"),
        DependencyCheck("pgvector", "ok", "postgres:extension/vector"),
        DependencyCheck("pg_search", "unavailable", "postgres:extension/pg_search", "missing"),
    ]))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "unavailable"
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_migrations.py backend/tests/test_infrastructure_config.py backend/tests/test_dependency_health.py -q`

Expected: FAIL，缺少迁移列、`check_pg_search_extension` 和 `/health/ready`。

- [ ] **Step 3: 实现数据库模型和 Alembic 迁移**

Add to `retrieval_logs` in `backend/agromech_api/db/models.py`:

```python
    Column("query_rewrite", JSON, nullable=False, default=dict, server_default=text("'{}'")),
    Column("fusion", JSON, nullable=False, default=dict, server_default=text("'{}'")),
```

Add the filter indexes next to the existing `documents` indexes:

```python
Index(
    "ix_documents_retrieval_state",
    documents.c.status,
    documents.c.deleted_at,
    documents.c.visibility,
    documents.c.owner_user_id,
)
Index(
    "ix_documents_retrieval_metadata",
    documents.c.document_type,
    documents.c.language,
    documents.c.document_version,
)
```

Create `backend/alembic/versions/0013_add_pg_search_bm25.py`:

```python
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0013_pg_search_bm25"
down_revision = "0012_pgvector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    columns = {column["name"] for column in sa.inspect(bind).get_columns("retrieval_logs")}
    with op.batch_alter_table("retrieval_logs") as batch:
        if "query_rewrite" not in columns:
            batch.add_column(sa.Column("query_rewrite", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        if "fusion" not in columns:
            batch.add_column(sa.Column("fusion", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    document_indexes = {index["name"] for index in sa.inspect(bind).get_indexes("documents")}
    if "ix_documents_retrieval_state" not in document_indexes:
        op.create_index(
            "ix_documents_retrieval_state",
            "documents",
            ["status", "deleted_at", "visibility", "owner_user_id"],
        )
    if "ix_documents_retrieval_metadata" not in document_indexes:
        op.create_index(
            "ix_documents_retrieval_metadata",
            "documents",
            ["document_type", "language", "document_version"],
        )
    if bind.dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_chunk_search_index_bm25
        ON chunk_search_index
        USING bm25 (
            id,
            chunk_id,
            document_id,
            chunk_type,
            (search_text::pdb.jieba)
        )
        WITH (key_field='id')
    """)


def downgrade() -> None:
    raise RuntimeError("Downgrade from pg_search BM25 storage is not supported")
```

- [ ] **Step 4: 实现 pg_search Readiness**

Add to `backend/agromech_api/core/infrastructure.py`:

```python
def check_pg_search_extension(engine=None) -> DependencyCheck:
    active_engine = engine or get_engine()
    target = "postgres:extension/pg_search"
    try:
        with active_engine.connect() as connection:
            row = connection.execute(text("""
                SELECT
                    EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_search') AS extension,
                    EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_chunk_search_index_bm25') AS index
            """)).mappings().one()
        if row["extension"] and row["index"]:
            return DependencyCheck("pg_search", "ok", target)
        return DependencyCheck("pg_search", "unavailable", target, "pg_search extension or BM25 index is missing")
    except Exception as exc:  # noqa: BLE001
        return DependencyCheck("pg_search", "unavailable", target, sanitize_database_error(exc))
```

Append `check_pg_search_extension(engine)` to `check_infrastructure()`.

Add to `backend/agromech_api/api/health.py`:

```python
    @app.get("/health/ready", tags=["system"])
    def readiness(response: Response) -> dict[str, object]:
        checks = dependency_checker()
        ready = all(check.status == "ok" for check in checks)
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if ready else "unavailable",
            "dependencies": [check.to_dict() for check in checks],
        }
```

Also import `Response` and `status` from FastAPI.

- [ ] **Step 5: 增加真实 PostgreSQL BM25 集成测试和 CI 服务**

Append to `backend/tests/test_bm25_retrieval.py`:

```python
import os
import pytest
from sqlalchemy import create_engine

from agromech_api.rag.retrieval.bm25 import PostgresBm25Retriever


@pytest.mark.skipif(not os.getenv("AGROMECH_TEST_POSTGRES_URL"), reason="PostgreSQL BM25 URL not configured")
def test_postgres_bm25_uses_pg_search_jieba_and_filters_before_limit() -> None:
    engine = create_engine(os.environ["AGROMECH_TEST_POSTGRES_URL"])
    # scripts/test-integration.sh upgrades a fresh CI database before this test.
    seed_bm25_rows(engine)
    chinese_hits = PostgresBm25Retriever().search(
        engine,
        "液压泵",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        limit=10,
    )
    filtered_hits = PostgresBm25Retriever().search(
        engine,
        "检查",
        filters=build_retrieval_filters(request_filters={"model": "M7040"}, viewer_user_id=None),
        limit=1,
    )
    assert [hit.chunk_id for hit in chinese_hits] == ["chunk-m"]
    assert [hit.chunk_id for hit in filtered_hits] == ["chunk-m"]
```

Add the same PostgreSQL service and test URL to the `test` job in `.github/workflows/ci.yml` and the `deploy` job in `.github/workflows/deploy.yml`:

```yaml
    services:
      postgres:
        image: paradedb/paradedb:0.24.2
        env:
          POSTGRES_USER: agromech
          POSTGRES_PASSWORD: agromech
          POSTGRES_DB: agromech_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U agromech -d agromech_test"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 10
    env:
      AGROMECH_TEST_POSTGRES_URL: postgresql+psycopg://agromech:agromech@localhost:5432/agromech_test
```

Add this step after Python dependency installation and before `Run checks` in both workflows, because `scripts/test-unit.sh` discovers all backend tests before the dedicated integration script runs:

```yaml
      - name: Prepare PostgreSQL search database
        env:
          DATABASE_URL: ${{ env.AGROMECH_TEST_POSTGRES_URL }}
        run: python -m alembic upgrade head
```

Before the pytest command in `scripts/test-integration.sh`, run:

```bash
if [[ -n "${AGROMECH_TEST_POSTGRES_URL:-}" ]]; then
  DATABASE_URL="$AGROMECH_TEST_POSTGRES_URL" "$PYTHON_BIN" -m alembic upgrade head
fi
```

Add `backend/tests/test_bm25_retrieval.py` and the dependency-health tests to the integration command.

- [ ] **Step 6: 运行迁移、健康和真实 PostgreSQL 测试**

Run local unit subset:

```bash
.venv/bin/python -m pytest backend/tests/test_migrations.py backend/tests/test_infrastructure_config.py backend/tests/test_dependency_health.py -q
```

Expected: PASS。

Run with a ParadeDB PostgreSQL instance:

```bash
DATABASE_URL="$AGROMECH_TEST_POSTGRES_URL" .venv/bin/python -m alembic upgrade head
AGROMECH_TEST_POSTGRES_URL="$AGROMECH_TEST_POSTGRES_URL" .venv/bin/python -m pytest backend/tests/test_bm25_retrieval.py -q
```

Expected: PASS；PostgreSQL test must not be skipped。

- [ ] **Step 7: 提交任务**

```bash
git add backend/alembic/versions/0013_add_pg_search_bm25.py backend/agromech_api/db/models.py backend/agromech_api/core/infrastructure.py backend/agromech_api/api/health.py backend/tests/test_migrations.py backend/tests/test_bm25_retrieval.py backend/tests/test_infrastructure_config.py backend/tests/test_dependency_health.py .github/workflows/ci.yml .github/workflows/deploy.yml scripts/test-integration.sh
git commit -m "feat: add postgres bm25 infrastructure"
```

---

### Task 6: 为 Dense 检索增加共享过滤并直接替换 Hybrid 融合

**Files:**
- Modify: `backend/agromech_api/rag/retrieval/indexing.py:245-452`
- Modify: `backend/agromech_api/rag/retrieval/hybrid.py:1-757`
- Modify: `backend/tests/test_hybrid_retrieval.py`
- Modify: `backend/tests/test_search_indexing.py`

**Interfaces:**
- Consumes: `Bm25Retriever`、`RetrievalFilters`、`RankedHit`、`rrf_fuse()` and existing rerank provider。
- Produces: `hybrid_retrieve_with_trace(..., filters: RetrievalFilters | None = None, query_rewrite: dict | None = None)` using channels `dense` and `bm25` only；未显式传入时构建匿名、无业务过滤的兼容默认值。
- Fused candidates must contain `score == rrf_score`, `channel_ranks`, raw channel scores, and vector references when present。

- [ ] **Step 1: 把旧加权融合测试改成目标行为测试**

Replace the keyword/structured channel assertions in `backend/tests/test_hybrid_retrieval.py` with:

```python
class FixedBm25Retriever:
    def search(self, _engine, _query, *, filters, limit):
        _ = filters, limit
        return [
            RankedHit(chunk_id="chunk-m7040", rank=1, score=8.0),
            RankedHit(chunk_id="chunk-l3901", rank=2, score=3.0),
        ]


def test_hybrid_retrieval_uses_dense_bm25_rrf_and_no_structured_channel(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        bm25_retriever=FixedBm25Retriever(),
        trace_id="trace-rrf",
    )

    first = result["candidates"][0]
    assert first["chunk_id"] == "chunk-m7040"
    assert set(first["channels"]) == {"bm25", "dense"}
    assert first["score"] == first["rrf_score"]
    assert first["channel_ranks"] == {"bm25": 1, "dense": 1}
    assert "structured" not in first["channels"]


def test_bm25_failure_degrades_to_dense_only(tmp_path) -> None:
    class FailingBm25Retriever:
        def search(self, *_args, **_kwargs):
            raise RuntimeError("bm25 unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = hybrid_retrieve_with_trace(
        engine,
        "dashboard hydraulic warning",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        bm25_retriever=FailingBm25Retriever(),
        trace_id="trace-bm25-degraded",
    )

    assert result["status"] == "ok"
    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-bm25-degraded")).mappings().one()
    assert {"channel": "bm25", "reason": "bm25_degraded"} in log["channels"]["degraded"]


def test_dense_failure_degrades_to_bm25_only(tmp_path) -> None:
    class FailingEmbeddingProvider:
        provider = "test"
        model = "failing"

        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=FixedBm25Retriever(),
        trace_id="trace-dense-degraded",
    )

    assert result["status"] == "ok"
    assert result["candidates"][0]["channels"] == ["bm25"]
    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-dense-degraded")).mappings().one()
    assert {"channel": "dense", "reason": "dense_degraded"} in log["channels"]["degraded"]


def test_both_retrieval_channels_failing_returns_evidence_insufficient(tmp_path) -> None:
    class FailingEmbeddingProvider:
        def embed(self, _query):
            raise RuntimeError("dense unavailable")

    class FailingBm25Retriever:
        def search(self, *_args, **_kwargs):
            raise RuntimeError("bm25 unavailable")

    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        filters=build_retrieval_filters(request_filters={}, viewer_user_id=None),
        embedding_provider=FailingEmbeddingProvider(),
        bm25_retriever=FailingBm25Retriever(),
        trace_id="trace-all-retrieval-degraded",
    )

    assert result["status"] == "evidence_insufficient"
    assert result["final_evidence"] == []
```

Add an explicit filter-before-limit regression:

```python
def test_dense_and_bm25_share_explicit_model_filter_before_top_k(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    filters = build_retrieval_filters(request_filters={"model": "M7040"}, viewer_user_id=None)

    result = hybrid_retrieve_with_trace(
        engine,
        "E01 repair",
        filters=filters,
        bm25_retriever=FixedBm25Retriever(),
        trace_id="trace-model-filter",
    )

    assert {candidate["document_id"] for candidate in result["candidates"]} == {"doc-m7040"}
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_hybrid_retrieval.py backend/tests/test_search_indexing.py -q`

Expected: FAIL，因为 Hybrid 仍输出 `keyword`/`structured` 并执行加权求和。

- [ ] **Step 3: 为向量检索接入共享过滤**

Change `vector_search()` and `postgres_vector_search()` signatures in `indexing.py`:

```python
def vector_search(
    engine: Engine,
    query: str,
    *,
    filters: RetrievalFilters,
    limit: int = 10,
    active_embedding_version: str | None = None,
    embedding_provider=None,
) -> list[dict[str, object]]:
```

Change the PostgreSQL helper to the same filter contract:

```python
def postgres_vector_search(
    engine: Engine,
    query_embedding: list[float],
    *,
    embedding_version: str,
    filters: RetrievalFilters,
    limit: int,
) -> list[dict[str, object]]:
```

Update the call from `vector_search()` to pass `filters=filters`.

Add to both PostgreSQL and SQLite statements before ordering/limiting:

```python
        .where(*document_filter_conditions(filters))
        .where(*chunk_filter_conditions(chunk_vector_embeddings.c.chunk_id, filters))
```

Remove the separate `viewer_user_id` argument; its value now comes from `RetrievalFilters`。

- [ ] **Step 4: 直接替换 Hybrid 召回与融合**

In `hybrid.py`:

- remove `CHANNEL_WEIGHTS`, `KeywordRetrievalAgent`, `StructuredRetrievalAgent`, `EvidenceMergeAgent`, `VisualRetrievalAgent`, `add_candidate()` and `add_channel()`;
- rename the text vector channel to `dense`;
- add `Bm25RetrievalAgent`;
- collect only `dense` and `bm25` concurrently;
- call `rrf_fuse()`;
- bulk-load all fused chunk payloads in one query;
- apply inferred applicability only as rerank features;
- use `rrf_score` as the deterministic rerank base score;
- after hydration, call `enforce_retrieval_filters()` so fake/degraded providers cannot bypass explicit document or subsystem filters;
- record one-channel degradation without failing the request;
- return evidence-insufficient when both channels are unavailable or empty.

Use this public signature and compatibility default:

```python
def hybrid_retrieve_with_trace(
    engine: Engine,
    query: str,
    *,
    trace_id: str | None = None,
    limit: int | None = None,
    logged_query: str | None = None,
    filters: RetrievalFilters | None = None,
    query_rewrite: dict[str, object] | None = None,
    degraded_channels: dict[str, str] | None = None,
    embedding_provider=None,
    bm25_retriever: Bm25Retriever | None = None,
    rerank_provider=None,
    rerank_top_k: int | None = None,
    settings=None,
) -> dict[str, object]:
    settings = settings or get_settings()
    filters = filters or build_retrieval_filters(request_filters={}, viewer_user_id=None)
    query_rewrite = dict(query_rewrite or {})
    final_limit = limit or settings.final_evidence_limit
```

Write `filters.as_trace()` to `retrieval_logs.filters`; never serialize `viewer_user_id` into the trace.

Keep `hybrid_retrieve(engine, query, *, limit=10, filters=None, ...)` as the non-Trace compatibility wrapper used by tests and evaluation tooling. It must execute the same Dense/BM25/RRF/Rerank implementation without writing `retrieval_logs`.

Use this orchestration shape:

```python
channel_hits, channel_status = collect_ranked_hits(
    engine,
    query,
    filters=filters,
    dense_top_k=settings.dense_top_k,
    bm25_top_k=settings.bm25_top_k,
    embedding_provider=embedding_provider,
    bm25_retriever=bm25_retriever or build_bm25_retriever(engine),
)
fused, fusion_trace = rrf_fuse(
    channel_hits,
    rrf_k=settings.rrf_k,
    weights={"dense": settings.rrf_dense_weight, "bm25": settings.rrf_bm25_weight},
    limit=settings.fusion_top_k,
)
candidates = hydrate_fused_candidates(engine, fused)
candidates = enforce_retrieval_filters(engine, candidates, filters=filters)
reranked, rerank_trace = RerankAgent().run(
    candidates,
    parsed,
    limit=final_limit,
    query=query,
    rerank_provider=rerank_provider,
    degraded_channels=degraded_channels,
    rerank_top_k=settings.rerank_top_k,
)
```

`hydrate_fused_candidates()` must perform one `WHERE document_chunks.id.in_(...)` query and copy:

```python
{
    "chunk_id": hit.chunk_id,
    "document_id": row["document_id"],
    "chunk_type": row["chunk_type"],
    "content": row["content"],
    "source_locator": row["source_locator"],
    "channels": sorted(hit.channel_ranks),
    "channel_ranks": dict(hit.channel_ranks),
    "channel_scores": dict(hit.channel_scores),
    "rrf_score": hit.rrf_score,
    "score": hit.rrf_score,
    "vector_ref": hit.vector_ref,
    "embedding_id": hit.embedding_id,
    "not_applicable": False,
}
```

`enforce_retrieval_filters()` must select allowed chunk IDs using the same `document_filter_conditions(filters)` and `chunk_filter_conditions(document_chunks.c.id, filters)` conditions, then retain only candidates whose `chunk_id` is allowed. This is the post-fusion fail-closed boundary for both authorization and explicit filters.

- [ ] **Step 5: 运行目标测试并删除旧行为测试**

Delete tests that assert `CHANNEL_WEIGHTS`, `keyword`, `structured`, or `vision` as text-retrieval fusion channels. Keep and update visibility, vector reference, rerank fallback, evidence-insufficient, and graph-disabled tests.

Run: `.venv/bin/python -m pytest backend/tests/test_hybrid_retrieval.py backend/tests/test_search_indexing.py backend/tests/test_rrf_fusion.py backend/tests/test_bm25_retrieval.py -q`

Expected: PASS。

- [ ] **Step 6: 提交任务**

```bash
git add backend/agromech_api/rag/retrieval/indexing.py backend/agromech_api/rag/retrieval/hybrid.py backend/tests/test_hybrid_retrieval.py backend/tests/test_search_indexing.py
git commit -m "feat: replace weighted retrieval with bm25 rrf"
```

---

### Task 7: 将 Query Rewrite 移到首次检索前并约束 Citation

**Files:**
- Modify: `backend/agromech_api/rag/agent/state.py:6-27`
- Modify: `backend/agromech_api/rag/agent/agents/query_rewrite.py:8-31`
- Modify: `backend/agromech_api/rag/agent/agents/retrieval.py:17-36`
- Modify: `backend/agromech_api/rag/agent/tools.py:10-29`
- Modify: `backend/agromech_api/rag/langchain/adapters.py:52-78`
- Modify: `backend/agromech_api/rag/agent/controller.py:10-47`
- Modify: `backend/agromech_api/rag/agent/graph.py:24-205`
- Modify: `backend/agromech_api/qa/text.py:102-318`
- Modify: `backend/agromech_api/rag/traces.py:1-111`
- Modify: `backend/tests/test_agent_controller.py`
- Modify: `backend/tests/test_langchain_adapters.py`
- Modify: `backend/tests/test_text_qa.py`

**Interfaces:**
- `AgentController(..., rewrite_fn: Callable[..., dict[str, object]])` injects rewrite behavior。
- `rewrite_fn` receives `question`, `parsed_query`, `filters`, and `supplemental`。
- Agent state stores `query_rewrite: dict[str, object]`, `rewritten_query: str`, and `retrieval_round: int`。
- Retrieval receives the rewritten query but Trace keeps the original question separately。
- `original_question` 与 `query_rewrite` 必须穿过 Agent Tool 和 LangChain Adapter，不能在适配层丢失。

- [ ] **Step 1: 写节点顺序、单次 LLM 和 Citation 不变量测试**

Add `from agromech_api.rag.retrieval.query_understanding import parse_query` and update `backend/tests/test_agent_controller.py`:

```python
def test_agent_controller_rewrites_before_first_retrieval() -> None:
    calls: list[str] = []
    controller = AgentController(
        parse_query_fn=lambda question, engine=None: calls.append("parse") or parse_query(question),
        rewrite_fn=lambda **kwargs: calls.append("rewrite") or {
            "query": "M7040 E01 hydraulic pump",
            "trace": {"provider": "test", "fallback": False},
        },
        retrieve_fn=lambda **kwargs: calls.append(f"retrieve:{kwargs['question']}") or {
            "status": "ok",
            "final_evidence": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
            "citations": [{"chunk_id": "chunk-1", "document_id": "doc-1"}],
        },
        answer_fn=lambda **kwargs: calls.append("answer") or {"answer": "ok", "citations": [], "trace_id": kwargs["trace_id"]},
    )

    controller.answer_text(engine=None, question="M7040 E01 怎么修？", trace_id="trace-1", filters={})

    assert calls[:3] == ["parse", "rewrite", "retrieve:M7040 E01 hydraulic pump"]


def test_agent_controller_uses_llm_once_then_rule_supplemental_rewrite() -> None:
    rewrite_modes: list[bool] = []
    retrieval_calls = 0

    def rewrite_fn(**kwargs):
        rewrite_modes.append(kwargs["supplemental"])
        return {"query": "first" if not kwargs["supplemental"] else "fallback", "trace": {"fallback": kwargs["supplemental"]}}

    def retrieve_fn(**_kwargs):
        nonlocal retrieval_calls
        retrieval_calls += 1
        return {"status": "evidence_insufficient", "final_evidence": [], "citations": []}

    controller = AgentController(
        parse_query_fn=lambda question, engine=None: parse_query(question),
        rewrite_fn=rewrite_fn,
        retrieve_fn=retrieve_fn,
        answer_fn=lambda **_kwargs: {"answer": "must not run"},
    )
    payload = controller.answer_text(engine=None, question="液压泵异响", trace_id="trace-2", filters={})

    assert rewrite_modes == [False, True]
    assert retrieval_calls == 2
    assert payload["citations"] == []
```

Append to `backend/tests/test_langchain_adapters.py`:

```python
def test_text_retriever_forwards_original_question_and_rewrite_trace() -> None:
    captured = {}

    def retrieve_payload(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "final_evidence": []}

    retriever = AgroMechTextRetriever(
        engine=None,
        retrieve_payload_fn=retrieve_payload,
        original_question="M7040 的 E01 怎么修？",
        query_rewrite={"provider": "bailian", "fallback": False},
    )
    retriever.retrieve_payload("M7040 E01 hydraulic pump")

    assert captured["question"] == "M7040 E01 hydraulic pump"
    assert captured["original_question"] == "M7040 的 E01 怎么修？"
    assert captured["query_rewrite"] == {"provider": "bailian", "fallback": False}
```

Add to `backend/tests/test_text_qa.py`:

```python
def test_text_qa_citations_exactly_match_final_reranked_evidence(tmp_path: Path) -> None:
    client, engine, token = qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    response = client.post(
        "/qa/text",
        headers=auth_header(token, "trace-final-citations"),
        json={"question": "M7040 E01 hydraulic pump repair"},
    )

    payload = response.json()
    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-final-citations")).mappings().one()
    assert [citation["chunk_id"] for citation in payload["citations"]] == [item["chunk_id"] for item in log["final_evidence"] if not item.get("not_applicable")]
    assert log["channels"]["citation"] == {
        "status": "ok",
        "count": len(payload["citations"]),
        "chunk_ids": [citation["chunk_id"] for citation in payload["citations"]],
        "asset_ids": [],
    }
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_agent_controller.py backend/tests/test_langchain_adapters.py backend/tests/test_text_qa.py -q`

Expected: FAIL，当前 Graph 在首次检索后才可能 Rewrite。

- [ ] **Step 3: 修改 Agent 状态和 QueryRewriteAgent**

Add to `AgentState`:

```python
    query_rewrite: dict[str, Any]
```

In `RetrievalAgent.run()`, include the active rewrite trace in the tool payload:

```python
                    "original_question": state["question"],
                    "query_rewrite": state.get("query_rewrite") or {},
```

In `build_text_retrieval_tool()`, pass both values into `AgroMechTextRetriever`:

```python
            original_question=str(payload.get("original_question") or payload.get("question") or ""),
            query_rewrite=dict(payload.get("query_rewrite") or {}),
```

Add the fields and forwarding behavior to `AgroMechTextRetriever`:

```python
    original_question: str = ""
    query_rewrite: dict[str, Any] = Field(default_factory=dict)

    def retrieve_payload(self, query: str, **overrides: Any) -> dict[str, Any]:
        return self.retrieve_payload_fn(
            engine=overrides.get("engine", self.engine),
            question=query,
            original_question=overrides.get("original_question", self.original_question),
            query_rewrite=overrides.get("query_rewrite", self.query_rewrite),
            filters=overrides.get("filters", self.filters),
            trace_id=overrides.get("trace_id", self.trace_id),
            route=overrides.get("route", self.route),
            image_context=overrides.get("image_context", self.image_context),
        )
```

Change `QueryRewriteAgent`:

```python
class QueryRewriteAgent:
    name = "QueryRewriteAgent"

    def __init__(self, rewrite_fn: Callable[..., dict[str, Any]]) -> None:
        self.rewrite_fn = rewrite_fn

    def run(self, state: AgentState) -> AgentResult:
        supplemental = int(state.get("retrieval_round", 0)) > 0
        rewritten = self.rewrite_fn(
            question=state["question"],
            parsed_query=state.get("parsed_query"),
            filters=state.get("filters") or {},
            supplemental=supplemental,
        )
        round_number = int(state.get("retrieval_round", 0)) + 1
        trace = rewritten["trace"]
        return {
            "status": "ok",
            "output": {"rewritten_query": rewritten["query"], "query_rewrite": trace, "retrieval_round": round_number},
            "trace": agent_trace(agent=self.name, step="rewrite", status="ok", decision="fallback" if trace.get("fallback") else "rewritten", reason=trace.get("reason"), round=round_number),
        }
```

- [ ] **Step 4: 修改 LangGraph 顺序和补检索条件**

Pass `rewrite_fn` into `build_agent_graph()` and construct `QueryRewriteAgent(rewrite_fn)`.

Rename the round cap so it describes both the initial and supplemental retrieval:

```python
MAX_RETRIEVAL_ROUNDS = 2
```

Set edges to:

```python
    graph.add_edge("parse", "route")
    graph.add_edge("route", "rewrite")
    graph.add_edge("rewrite", "retrieve")
    graph.add_edge("retrieve", "planner")
    graph.add_edge("planner", "evidence_check")
```

Use one post-check branch:

```python
def after_evidence_check(state: AgentState) -> str:
    check = state.get("evidence_check") or {}
    if check.get("status") == "sufficient":
        return "domain"
    if int(state.get("retrieval_round", 0)) < MAX_RETRIEVAL_ROUNDS:
        return "rewrite"
    return "domain"
```

Keep visual retrieval inside `planner_node` as today; do not add Graph RAG.

- [ ] **Step 5: 在 QA 组装层注入 Rewrite 和共享过滤**

In `build_text_agent_controller()`:

```python
    rewrite_provider = build_query_rewrite_provider(settings)
    return AgentController(
        parse_query_fn=lambda question, engine=None: parse_query(question, engine=engine),
        rewrite_fn=lambda **kwargs: rewrite_for_text_agent(settings=settings, provider=rewrite_provider, **kwargs),
        retrieve_fn=lambda **kwargs: retrieve_for_text_agent(settings=settings, viewer_user_id=viewer_user_id, **kwargs),
        planner_fn=lambda **kwargs: planner_for_text_agent(settings=settings, **kwargs),
        visual_retrieve_fn=lambda **kwargs: retrieve_visual_for_text_agent(settings=settings, viewer_user_id=viewer_user_id, **kwargs),
        answer_fn=lambda **kwargs: answer_for_text_agent(settings=settings, **kwargs),
        multimodal_answer_fn=lambda **kwargs: answer_for_text_agent(settings=settings, **kwargs),
    )
```

Add:

```python
def rewrite_for_text_agent(*, settings: Settings, provider, question: str, parsed_query, filters: dict[str, str | None], supplemental: bool, **_kwargs) -> dict[str, object]:
    result = rewrite_query(
        question=question,
        parsed=parsed_query,
        request_filters=filters,
        provider=provider,
        supplemental=supplemental,
    )
    return {"query": result.query, "trace": result.to_trace()}
```

Use this `retrieve_for_text_agent()` boundary:

```python
def retrieve_for_text_agent(
    *,
    settings: Settings,
    engine: Engine,
    question: str,
    original_question: str,
    filters: dict[str, str | None],
    query_rewrite: dict[str, object],
    trace_id: str,
    viewer_user_id: str | None = None,
    **_kwargs,
) -> dict[str, object]:
    retrieval_filters = build_retrieval_filters(
        request_filters=filters,
        viewer_user_id=viewer_user_id,
    )
    return hybrid_retrieve_with_trace(
        engine,
        query_with_filters(question, filters),
        trace_id=trace_id,
        logged_query=original_question,
        filters=retrieval_filters,
        query_rewrite=query_rewrite,
        embedding_provider=build_embedding_provider(settings),
        rerank_provider=build_rerank_provider(settings) if settings.rerank_enabled else None,
        settings=settings,
    )
```

The function may then apply the existing final-evidence limit and build per-round citations for the evidence checker as it does today. `original_question` is mandatory in the tool payload so a rewritten query is never stored as the user query. Per-round citations must not be written as the final Citation Trace yet.

In `answer_for_text_agent()`, remove `not_applicable` evidence, apply `settings.final_evidence_limit`, build text and visual citations separately, and restore final-evidence order. The Citation set is valid only when every final evidence item maps to exactly one Citation:

```python
    if retrieval["status"] == "evidence_insufficient":
        return evidence_insufficient_answer(trace_id)

    final_evidence = [
        item for item in final_evidence if not item.get("not_applicable")
    ][: settings.final_evidence_limit]
    trim_retrieval_final_evidence(engine, trace_id=trace_id, final_evidence=final_evidence)
    if not final_evidence:
        return evidence_insufficient_answer(trace_id)

    text_citations = {
        str(item["chunk_id"]): item
        for item in build_citations(engine, [item for item in final_evidence if item.get("chunk_id")])
    }
    visual_citations = {
        str(item["asset_id"]): item
        for item in build_visual_citations(engine, [item for item in final_evidence if item.get("asset_id")])
    }
    citations = []
    for evidence in final_evidence:
        citation = (
            text_citations.get(str(evidence["chunk_id"]))
            if evidence.get("chunk_id")
            else visual_citations.get(str(evidence.get("asset_id")))
        )
        if citation is not None:
            citations.append(citation)
    if not citations or len(citations) != len(final_evidence):
        return evidence_insufficient_answer(trace_id)
```

Add `record_citation_trace(engine, trace_id, citations)` to `rag/traces.py`. It must load the existing `channels` JSON, set this value, and update the same retrieval row:

```python
channels["citation"] = {
    "status": "ok" if citations else "insufficient",
    "count": len(citations),
    "chunk_ids": [str(item["chunk_id"]) for item in citations if item.get("chunk_id")],
    "asset_ids": [str(item["asset_id"]) for item in citations if item.get("asset_id")],
}
```

Call it in `answer_text_question()` immediately after `controller.answer_text()` returns and before `record_qa()`:

```python
    record_citation_trace(engine, trace_id, list(payload.get("citations") or []))
```

Do not write final Citation status from `retrieve_for_text_agent()` or `answer_for_text_agent()`. `AnswerWriterAgent` can block generation before `answer_for_text_agent()` is called, so the QA orchestration boundary is the only point shared by successful answers, final evidence-insufficient responses, supplemental retrieval, and visual-evidence merging. This makes Citation Trace match the actual response without adding another database column.

- [ ] **Step 6: 运行 Agent 与 QA 测试**

Run: `.venv/bin/python -m pytest backend/tests/test_agent_controller.py backend/tests/test_langchain_adapters.py backend/tests/test_text_qa.py backend/tests/test_query_rewrite.py -q`

Expected: PASS。

- [ ] **Step 7: 提交任务**

```bash
git add backend/agromech_api/rag/agent/state.py backend/agromech_api/rag/agent/agents/query_rewrite.py backend/agromech_api/rag/agent/agents/retrieval.py backend/agromech_api/rag/agent/tools.py backend/agromech_api/rag/langchain/adapters.py backend/agromech_api/rag/agent/controller.py backend/agromech_api/rag/agent/graph.py backend/agromech_api/qa/text.py backend/agromech_api/rag/traces.py backend/tests/test_agent_controller.py backend/tests/test_langchain_adapters.py backend/tests/test_text_qa.py
git commit -m "feat: rewrite queries before retrieval"
```

---

### Task 8: 扩展 Retrieval Trace 并保证角色脱敏

**Files:**
- Modify: `backend/agromech_api/rag/retrieval/hybrid.py`
- Modify: `backend/agromech_api/rag/traces.py:43-67`
- Modify: `backend/tests/test_retrieval_trace.py`

**Interfaces:**
- `write_retrieval_log()` consumes `query_rewrite: dict[str, object]` and `fusion: dict[str, object]`。
- A single `retrieval_logs` row is maintained per unique `trace_id`; each additional retrieval round appends to `query_rewrite.attempts` and `fusion.attempts`, then replaces each `final` value。
- `fusion.retrieval_duration_ms` is the sum of Dense/BM25/RRF/Rerank durations for all attempts；端到端检索延迟还需加上各次 Query Rewrite 的 `duration_ms`，但不包含答案生成。
- Evaluator/Admin receive full Rewrite and Fusion payloads。
- User/Maintainer receive only Rewrite status/fallback and Fusion configuration/counts, never candidate contents or rewritten-query internals。

- [ ] **Step 1: 写完整 Trace 与普通用户摘要测试**

Update `backend/tests/test_retrieval_trace.py`:

```python
def test_retrieve_trace_records_rewrite_and_rrf_fusion(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = hybrid_retrieve_with_trace(
        engine,
        "M7040 E01 hydraulic pump",
        trace_id="trace-fusion",
        query_rewrite={"original_query": "M7040 的 E01 怎么修？", "query": "M7040 E01 hydraulic pump", "provider": "bailian", "model": "qwen3.6-flash", "fallback": False, "reason": "model_rewrite"},
    )
    assert result["status"] == "ok"
    with engine.connect() as connection:
        log = connection.execute(select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-fusion")).mappings().one()
    assert log["query_rewrite"]["final"]["provider"] == "bailian"
    assert log["query_rewrite"]["final"]["original_query"] == "M7040 的 E01 怎么修？"
    assert log["query_rewrite"]["final"]["query"] == "M7040 E01 hydraulic pump"
    assert len(log["query_rewrite"]["attempts"]) == 1
    assert log["fusion"]["final"]["rrf_k"] == 60
    assert log["fusion"]["retrieval_duration_ms"] >= 0
    assert {"dense", "bm25"}.issuperset(log["channels"]["used"])
    assert "channel_ranks" in log["fusion"]["final"]["items"][0]


def test_standard_user_trace_hides_rewritten_query_and_fused_items(tmp_path: Path) -> None:
    client, engine, token = trace_client(tmp_path, role=UserRole.USER)
    with engine.begin() as connection:
        connection.execute(insert(retrieval_logs).values(
            id="log-rrf",
            trace_id="trace-rrf-summary",
            query="M7040 E01",
            filters={"model": "M7040"},
            channels={"used": ["dense", "bm25"], "degraded": []},
            model_config={},
            query_rewrite={"attempts": [{"query": "secret internal rewrite", "provider": "bailian", "fallback": False}], "final": {"query": "secret internal rewrite", "provider": "bailian", "fallback": False}},
            fusion={"attempts": [{"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]}], "final": {"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}, "items": [{"chunk_id": "chunk-a"}]}, "retrieval_duration_ms": 12.0},
            candidates=[],
            rerank={},
            final_evidence=[],
        ))
    payload = client.get("/retrieval-traces/trace-rrf-summary", headers=auth_header(token)).json()
    assert payload["query_rewrite"] == {"provider": "bailian", "fallback": False}
    assert payload["fusion"] == {"rrf_k": 60, "channel_counts": {"dense": 50, "bm25": 50}}
    assert "items" not in payload["fusion"]
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_retrieval_trace.py -q`

Expected: FAIL，Trace 尚未写入或返回新字段。

- [ ] **Step 3: 写入 Rewrite/Fusion Trace 和目标模型配置**

Replace insert-only logging with an insert-or-update transaction. Add this helper:

```python
def append_trace_attempt(existing: dict[str, object] | None, current: dict[str, object]) -> dict[str, object]:
    previous = dict(existing or {})
    attempts = list(previous.get("attempts") or [])
    attempts.append(dict(current))
    return {"attempts": attempts, "final": dict(current)}
```

In `write_retrieval_log()`, select the row by `trace_id` inside the transaction. Build:

```python
        rewrite_payload = append_trace_attempt(existing["query_rewrite"] if existing else None, query_rewrite)
        fusion_payload = append_trace_attempt(existing["fusion"] if existing else None, fusion)
        fusion_payload["retrieval_duration_ms"] = round(
            sum(float(item.get("retrieval_duration_ms", 0.0)) for item in fusion_payload["attempts"]),
            3,
        )
```

If no row exists, insert the normal log values plus:

```python
                query_rewrite=rewrite_payload,
                fusion=fusion_payload,
```

If a row already exists, update `channels`, `model_config`, `candidates`, `rerank`, `final_evidence`, `query_rewrite`, and `fusion` on that row. Do not insert a second row with the same `trace_id`.

At the start of `hybrid_retrieve_with_trace()`, capture `started = time.perf_counter()`. Immediately before `write_retrieval_log()`, add the duration to the current attempt:

```python
    fusion_trace["retrieval_duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
```

Extend `trace_model_config()`:

```python
        "bm25_backend": "pg_search",
        "bm25_top_k": settings.bm25_top_k,
        "dense_top_k": settings.dense_top_k,
        "rrf_k": settings.rrf_k,
        "rrf_dense_weight": settings.rrf_dense_weight,
        "rrf_bm25_weight": settings.rrf_bm25_weight,
        "fusion_top_k": settings.fusion_top_k,
        "query_rewrite_model": settings.query_rewrite_model if settings.query_rewrite_enabled else None,
```

Trace candidate fields must use `rrf_score`, `channel_ranks`, and `channel_scores` instead of removed weighted channel scores.

- [ ] **Step 4: 按角色返回 Trace**

In `retrieval_trace_payload()` add full fields for `FULL_TRACE_ROLES`:

```python
                "query_rewrite": row["query_rewrite"] or {},
                "fusion": row["fusion"] or {},
```

For standard users and maintainers add only:

```python
    rewrite = (row["query_rewrite"] or {}).get("final", {})
    fusion = (row["fusion"] or {}).get("final", {})
    payload["query_rewrite"] = {key: rewrite[key] for key in ("provider", "fallback") if key in rewrite}
    payload["fusion"] = {key: fusion[key] for key in ("rrf_k", "channel_counts") if key in fusion}
```

Continue passing the final payload through `sanitize_trace_payload()`.

- [ ] **Step 5: 运行 Trace 测试并确认通过**

Run: `.venv/bin/python -m pytest backend/tests/test_retrieval_trace.py -q`

Expected: PASS。

- [ ] **Step 6: 提交任务**

```bash
git add backend/agromech_api/rag/retrieval/hybrid.py backend/agromech_api/rag/traces.py backend/tests/test_retrieval_trace.py
git commit -m "feat: trace query rewrite and rrf fusion"
```

---

### Task 9: 增加 Recall@20、nDCG@10 与检索验收指标

**Files:**
- Create: `scripts/evaluate-retrieval.py`
- Modify: `backend/agromech_api/evaluation/runner.py:88-243`
- Modify: `backend/tests/test_evaluation_runner.py`

**Interfaces:**
- Produces per-question `retrieved_sources`, `recall_at_20`, `ndcg_at_10`, `has_protected_identifiers`, `protected_identifiers_preserved`, `unauthorized_final_evidence`, `wrong_model_final_evidence`, and `retrieval_duration_ms`。
- `metrics_summary` adds `recall_at_20`, `ndcg_at_10`, `protected_identifier_cases`, `protected_identifier_preservation`, `unauthorized_final_evidence`, `explicit_model_confusion`, and `retrieval_p95_ms` while retaining existing metrics。
- Relevance is binary: expected `chunk_id` when present, otherwise expected `document_id`。

- [ ] **Step 1: 写指标公式和 Evaluation Runner 失败测试**

Append to `backend/tests/test_evaluation_runner.py`:

```python
from agromech_api.evaluation.runner import ndcg_at_k, recall_at_k


def test_recall_at_k_uses_expected_chunk_or_document_ids() -> None:
    retrieved = [
        {"chunk_id": "chunk-x", "document_id": "doc-x"},
        {"chunk_id": "chunk-a", "document_id": "doc-a"},
    ]
    expected = [{"chunk_id": "chunk-a", "document_id": "doc-a"}, {"document_id": "doc-b"}]
    assert recall_at_k(retrieved, expected, k=20) == 0.5


def test_ndcg_at_k_rewards_earlier_relevant_evidence() -> None:
    expected = [{"document_id": "doc-a"}, {"document_id": "doc-b"}]
    early = [{"document_id": "doc-a"}, {"document_id": "doc-x"}, {"document_id": "doc-b"}]
    late = [{"document_id": "doc-x"}, {"document_id": "doc-a"}, {"document_id": "doc-b"}]
    assert ndcg_at_k(early, expected, k=10) > ndcg_at_k(late, expected, k=10)


def test_ndcg_at_k_does_not_count_the_same_expected_document_twice() -> None:
    expected = [{"document_id": "doc-a"}]
    retrieved = [
        {"chunk_id": "a-1", "document_id": "doc-a"},
        {"chunk_id": "a-2", "document_id": "doc-a"},
    ]
    assert ndcg_at_k(retrieved, expected, k=10) == 1.0


def test_evaluation_summary_includes_retrieval_metrics(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)
    result = run_evaluation(
        engine,
        [EvaluationQuestion(question_id="q1", question="M7040 E01 hydraulic pump", category="fault", expected_sources=[{"document_id": "doc-m7040", "chunk_id": "chunk-m7040"}], expected_model="M7040")],
        dataset_version="curated-v2",
        model_config={},
        prompt_version="p1",
        settings=evaluation_settings(tmp_path),
    )
    assert result.metrics_summary["recall_at_20"] == 1.0
    assert result.metrics_summary["ndcg_at_10"] == 1.0
    assert result.metrics_summary["protected_identifier_cases"] == 1
    assert result.metrics_summary["protected_identifier_preservation"] == 1.0
    assert result.metrics_summary["unauthorized_final_evidence"] == 0
    assert result.metrics_summary["explicit_model_confusion"] == 0
    assert result.metrics_summary["retrieval_p95_ms"] >= 0
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_evaluation_runner.py -q`

Expected: FAIL，缺少新指标函数和汇总字段。

- [ ] **Step 3: 实现 Recall 与 nDCG**

Add to `backend/agromech_api/evaluation/runner.py`:

```python
import math


def source_key(source: dict[str, object]) -> tuple[str, str]:
    if source.get("chunk_id"):
        return "chunk", str(source["chunk_id"])
    return "document", str(source["document_id"])


def source_is_relevant(candidate: dict[str, object], expected: dict[str, object]) -> bool:
    kind, value = source_key(expected)
    return str(candidate.get("chunk_id" if kind == "chunk" else "document_id")) == value


def recall_at_k(retrieved: list[dict[str, object]], expected: list[dict[str, object]], *, k: int) -> float:
    if not expected:
        return 0.0
    matched = sum(1 for source in expected if any(source_is_relevant(candidate, source) for candidate in retrieved[:k]))
    return matched / len(expected)


def ndcg_at_k(retrieved: list[dict[str, object]], expected: list[dict[str, object]], *, k: int) -> float:
    if not expected:
        return 0.0
    remaining = list(expected)
    gains = []
    for candidate in retrieved[:k]:
        match = next((source for source in remaining if source_is_relevant(candidate, source)), None)
        gains.append(1.0 if match else 0.0)
        if match:
            remaining.remove(match)
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(len(expected), k)))
    return dcg / ideal if ideal else 0.0
```

Add `DocumentStatus` from `agromech_api.db.enums` and add `documents` and `retrieval_logs` to the imports from `agromech_api.db.models`. In `evaluate_question()`, read the retrieval log by `answer["trace_id"]`, rebuild the post-Rerank order from `rerank.items[].after_rank`, and compute:

For evaluation questions with `expected_model`, call `answer_text_question()` with an explicit model filter so `explicit_model_confusion` measures the hard-filter contract rather than inferred applicability:

```python
        filters={"model": question.expected_model} if question.expected_model else None,
```

```python
    with engine.connect() as connection:
        retrieval_log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == answer["trace_id"])
        ).mappings().one()
    rewrite_container = retrieval_log["query_rewrite"] or {}
    rewrite = rewrite_container.get("final", {})
    rewrite_duration_ms = sum(
        float(item.get("duration_ms", 0.0))
        for item in rewrite_container.get("attempts", [])
    )
    protected = [str(value) for value in rewrite.get("protected_identifiers", [])]
    rewritten_query = str(rewrite.get("query") or question.question)
    protected_preserved = all(value.lower() in rewritten_query.lower() for value in protected)
    candidates = list(retrieval_log["candidates"] or [])
    candidates_by_chunk = {
        str(item["chunk_id"]): item for item in candidates if item.get("chunk_id")
    }
    rerank_items = sorted(
        (retrieval_log["rerank"] or {}).get("items", []),
        key=lambda item: int(item.get("after_rank", 10**9)),
    )
    retrieved = [
        candidates_by_chunk[str(item["chunk_id"])]
        for item in rerank_items
        if str(item.get("chunk_id")) in candidates_by_chunk
    ]
    if not retrieved:
        retrieved = candidates
    final_document_ids = {str(item["document_id"]) for item in retrieval_log["final_evidence"] or [] if item.get("document_id")}
    with engine.connect() as connection:
        final_documents = connection.execute(
            select(
                documents.c.id,
                documents.c.visibility,
                documents.c.status,
                documents.c.deleted_at,
                documents.c.model,
            ).where(
                documents.c.id.in_(final_document_ids)
            )
        ).mappings().all() if final_document_ids else []
    unauthorized = [
        row
        for row in final_documents
        if row["visibility"] != "public"
        or row["status"] != DocumentStatus.INDEXED.value
        or row["deleted_at"] is not None
    ]
    unauthorized_count = len(final_document_ids) - len(final_documents) + len(unauthorized)
    wrong_model = [
        row
        for row in final_documents
        if question.expected_model and str(row["model"] or "").lower() != question.expected_model.lower()
    ]
```

Add to the question result:

```python
        "retrieved_sources": retrieved,
        "recall_at_20": recall_at_k(retrieved, question.expected_sources, k=20),
        "ndcg_at_10": ndcg_at_k(retrieved, question.expected_sources, k=10),
        "has_protected_identifiers": bool(protected),
        "protected_identifiers_preserved": protected_preserved,
        "unauthorized_final_evidence": unauthorized_count,
        "wrong_model_final_evidence": len(wrong_model),
        "retrieval_duration_ms": rewrite_duration_ms + float(
            (retrieval_log["fusion"] or {}).get("retrieval_duration_ms", 0.0)
        ),
```

In `metrics_for()`, first select questions that actually contain protected identifiers:

```python
    protected_scored = [result for result in question_results if result["has_protected_identifiers"]]
```

Extend the `total == 0` return with zero values for every new summary field so empty programmatic runs keep a stable schema:

```python
            "recall_at_20": 0.0,
            "ndcg_at_10": 0.0,
            "protected_identifier_cases": 0,
            "protected_identifier_preservation": 0.0,
            "unauthorized_final_evidence": 0,
            "explicit_model_confusion": 0,
            "retrieval_p95_ms": 0.0,
```

Then average and aggregate the new values:

```python
        "recall_at_20": sum(result["recall_at_20"] for result in source_scored) / len(source_scored) if source_scored else 0.0,
        "ndcg_at_10": sum(result["ndcg_at_10"] for result in source_scored) / len(source_scored) if source_scored else 0.0,
        "protected_identifier_cases": len(protected_scored),
        "protected_identifier_preservation": ratio(protected_scored, "protected_identifiers_preserved"),
        "unauthorized_final_evidence": sum(int(result["unauthorized_final_evidence"]) for result in question_results),
        "explicit_model_confusion": sum(int(result["wrong_model_final_evidence"]) for result in question_results),
        "retrieval_p95_ms": percentile([float(result["retrieval_duration_ms"]) for result in question_results], 0.95),
```

Add:

```python
def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * probability) - 1)]
```

Extend `model_config_from_settings()` with all BM25/RRF/Rewrite configuration values.

Create `scripts/evaluate-retrieval.py`:

```python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from agromech_api.core.config import get_settings  # noqa: E402
from agromech_api.core.database import get_engine  # noqa: E402
from agromech_api.evaluation.runner import run_evaluation_dataset  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate AgroMech retrieval quality.")
    parser.add_argument("--dataset", default="curated-mvp")
    parser.add_argument("--prompt-version", default="retrieval-v2")
    parser.add_argument("--baseline", type=Path)
    return parser.parse_args()


def assert_acceptance(metrics: dict[str, float], baseline: dict[str, float]) -> None:
    if metrics["protected_identifier_cases"] <= 0:
        raise SystemExit("evaluation dataset must contain protected identifiers")
    if metrics["protected_identifier_preservation"] != 1.0:
        raise SystemExit("protected identifier preservation must equal 1.0")
    if metrics["unauthorized_final_evidence"] != 0:
        raise SystemExit("unauthorized final evidence must equal 0")
    if metrics["explicit_model_confusion"] != 0:
        raise SystemExit("explicit model confusion must equal 0")
    if metrics["recall_at_20"] < baseline["recall_at_20"]:
        raise SystemExit("Recall@20 regressed")
    if metrics["ndcg_at_10"] < baseline["ndcg_at_10"]:
        raise SystemExit("nDCG@10 regressed")
    if metrics["recall_at_20"] == baseline["recall_at_20"] and metrics["ndcg_at_10"] == baseline["ndcg_at_10"]:
        raise SystemExit("Recall@20 or nDCG@10 must improve")
    if metrics["retrieval_p95_ms"] > baseline["retrieval_p95_ms"] * 1.5:
        raise SystemExit("retrieval P95 exceeded 1.5x baseline")


def main() -> int:
    args = parse_args()
    result = run_evaluation_dataset(
        get_engine(),
        settings=get_settings(),
        dataset_version=args.dataset,
        prompt_version=args.prompt_version,
    )
    metrics = result.metrics_summary
    print(json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True))
    if args.baseline:
        assert_acceptance(metrics, json.loads(args.baseline.read_text(encoding="utf-8")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行 Evaluation 测试并确认通过**

Run: `.venv/bin/python -m pytest backend/tests/test_evaluation_runner.py -q`

Expected: PASS。

- [ ] **Step 5: 提交任务**

```bash
git add backend/agromech_api/evaluation/runner.py backend/tests/test_evaluation_runner.py scripts/evaluate-retrieval.py
git commit -m "feat: measure retrieval recall and ndcg"
```

---

### Task 10: 更新重建脚本、配置模板、部署文档并完成全量验证

**Files:**
- Modify: `scripts/rebuild_vector_index.py`
- Modify: `scripts/rebuild-vector-index.py`
- Modify: `backend/tests/test_rebuild_vector_index.py`
- Modify: `.env.example`
- Modify: `deploy/env.prod.example`
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/prd.md`
- Modify: `docs/api-spec.md`
- Modify: `docs/tech-design.md`
- Modify: `docs/database-design.md`
- Modify: `docs/deployment.md`
- Modify: `docs/history.md`
- Modify: `backend/tests/test_docs_sync.py`

**Interfaces:**
- Rebuild command must repopulate `chunk_search_index` and `chunk_vector_embeddings` and then verify `ix_chunk_search_index_bm25` on PostgreSQL。
- Deployment runbook must fail before app rollout when `vector`, `pg_search`, or the BM25 index is missing。
- Documentation must use the target names `Dense`、`BM25`、`RRF`、`Rerank`、`Query Rewrite` and `Citation`。

- [ ] **Step 1: 写重建输出与文档同步失败测试**

Add a `CountingIndexer` test double that returns `IndexResult(chunk_count=3)`, then add this test to `backend/tests/test_rebuild_vector_index.py`:

```python
def test_rebuild_summary_reports_search_vector_rows_and_bm25_index(tmp_path) -> None:
    class CountingIndexer(RecordingIndexer):
        def index_document(self, document_id: str):
            super().index_document(document_id)
            return IndexResult(chunk_count=3)

    engine = make_engine(tmp_path)
    seed_document(engine, document_id="doc-a", status=DocumentStatus.INDEXED.value)
    summary = rebuild_vector_index(
        engine,
        include_visual=False,
        search_indexer_factory=CountingIndexer,
    )

    assert summary.search_rows_rebuilt == 3
    assert summary.vector_rows_rebuilt == 3
    assert summary.bm25_index == "ix_chunk_search_index_bm25"
```

Add `from agromech_api.rag.retrieval.indexing import IndexResult` to the test imports.

Update `backend/tests/test_docs_sync.py`:

```python
def test_docs_describe_dense_bm25_rrf_pipeline_and_pg_search() -> None:
    root = Path(__file__).parents[2]
    tech = (root / "docs/tech-design.md").read_text()
    database = (root / "docs/database-design.md").read_text()
    deployment = (root / "docs/deployment.md").read_text()
    prd = (root / "docs/prd.md").read_text()
    api = (root / "docs/api-spec.md").read_text()

    assert "Dense + BM25" in tech
    assert "RRF" in tech
    assert "pg_search" in database
    assert "ix_chunk_search_index_bm25" in database
    assert "FROM pg_extension" in deployment
    assert "/health/ready" in deployment
    assert "Dense + BM25" in prd
    assert "pg_search" in api
    assert "/health/ready" in api
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `.venv/bin/python -m pytest backend/tests/test_rebuild_vector_index.py backend/tests/test_docs_sync.py -q`

Expected: FAIL，脚本与文档仍描述旧关键词加权流程。

- [ ] **Step 3: 更新重建脚本和环境模板**

Keep the script name for compatibility. Extend `RebuildSummary`:

```python
@dataclass
class RebuildSummary:
    selected: int
    succeeded: int
    failed: int
    failures: list[tuple[str, str]]
    search_rows_rebuilt: int = 0
    vector_rows_rebuilt: int = 0
    bm25_index: str = "ix_chunk_search_index_bm25"
```

Initialize both row counters before the document loop. Capture the result of `search_indexer.index_document()` and add its `chunk_count` to both counters because `SearchIndexer` writes one `chunk_search_index` row and one `chunk_vector_embeddings` row per searchable chunk:

```python
            index_result = search_indexer.index_document(selected_document_id)
            if index_result is not None:
                search_rows_rebuilt += index_result.chunk_count
                vector_rows_rebuilt += index_result.chunk_count
```

Pass the counters into the final `RebuildSummary(...)`. Dry-run summaries keep both values at zero.

Update `scripts/rebuild-vector-index.py` to print the new fields:

```python
    print(
        f"selected={summary.selected} succeeded={summary.succeeded} failed={summary.failed} "
        f"search_rows_rebuilt={summary.search_rows_rebuilt} "
        f"vector_rows_rebuilt={summary.vector_rows_rebuilt} "
        f"bm25_index={summary.bm25_index}"
    )
```

On PostgreSQL, verify after rebuilding:

```python
    if engine.dialect.name == "postgresql":
        with engine.connect() as connection:
            index_present = connection.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_indexes WHERE indexname = 'ix_chunk_search_index_bm25')")
            ).scalar_one()
        if not index_present:
            raise RuntimeError("BM25 index ix_chunk_search_index_bm25 is missing")
```

Add `text` to the SQLAlchemy imports in `scripts/rebuild_vector_index.py`.

Add this exact block to `.env.example` and `deploy/env.prod.example`:

```dotenv
BM25_TOP_K=50
DENSE_TOP_K=50
RRF_K=60
RRF_DENSE_WEIGHT=1.0
RRF_BM25_WEIGHT=1.0
FUSION_TOP_K=30
RERANK_TOP_K=30
FINAL_EVIDENCE_LIMIT=5
QUERY_REWRITE_ENABLED=true
QUERY_REWRITE_MODEL=qwen3.6-flash
QUERY_REWRITE_TIMEOUT_SECONDS=10
```

- [ ] **Step 4: 更新架构和部署文档**

Document these exact operational checks in `docs/deployment.md`:

```sql
SELECT extname
FROM pg_extension
WHERE extname IN ('vector', 'pg_search')
ORDER BY extname;

SELECT indexname
FROM pg_indexes
WHERE indexname = 'ix_chunk_search_index_bm25';
```

Document the release order:

```text
record baseline -> backup -> install extensions -> alembic upgrade
-> rebuild indexes -> Dense/BM25/RRF smoke test -> deploy app
-> /health/ready -> QA/Citation smoke test -> monitor
```

Update architecture docs and `docs/prd.md` to remove claims that token overlap, structured recall, channel weights, or post-retrieval LLM rewrite are current behavior. Keep Graph RAG explicitly disabled. Update `docs/api-spec.md` so dependency health includes `pg_search` and the new `/health/ready` response/status contract.

- [ ] **Step 5: 运行任务级测试**

Run: `.venv/bin/python -m pytest backend/tests/test_rebuild_vector_index.py backend/tests/test_docs_sync.py -q`

Expected: PASS。

- [ ] **Step 6: 运行完整后端与 Worker 测试**

Run:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests worker/tests -q
```

Expected: PASS with 0 failures。

- [ ] **Step 7: 运行真实 PostgreSQL 集成测试**

Run:

```bash
AGROMECH_TEST_POSTGRES_URL="$AGROMECH_TEST_POSTGRES_URL" scripts/test-integration.sh
```

Expected: PASS；`test_postgres_bm25_uses_pg_search_jieba_and_filters_before_limit` must run and must not be skipped。

- [ ] **Step 8: 运行前端测试和构建以验证 API 兼容性**

Run:

```bash
npm run test --prefix frontend
npm run build --prefix frontend
```

Expected: PASS with 0 test failures and successful static build。若仓库已有与本改造无关的前端失败，记录完整失败名称，不修改不相关前端代码。

- [ ] **Step 9: 执行检索验收评估**

Before switching the implementation, retain the recorded baseline run. Run the same curated dataset after implementation:

```bash
.venv/bin/python scripts/evaluate-retrieval.py \
  --dataset curated-mvp \
  --baseline /tmp/agromech-retrieval-baseline.json
```

Expected:

```text
protected_identifier_preservation = 1.0
unauthorized_final_evidence = 0
explicit_model_confusion = 0
recall_at_20 >= baseline.recall_at_20
ndcg_at_10 >= baseline.ndcg_at_10
at least one of recall_at_20 or ndcg_at_10 is greater than baseline
retrieval_p95_ms <= baseline.retrieval_p95_ms * 1.5
```

- [ ] **Step 10: 提交任务**

```bash
git add scripts/rebuild_vector_index.py scripts/rebuild-vector-index.py backend/tests/test_rebuild_vector_index.py .env.example deploy/env.prod.example README.md docs/README.md docs/prd.md docs/api-spec.md docs/tech-design.md docs/database-design.md docs/deployment.md docs/history.md backend/tests/test_docs_sync.py
git commit -m "docs: document bm25 rrf retrieval operations"
```

---

## 最终检查清单

- [ ] `rg -n 'CHANNEL_WEIGHTS|KeywordRetrievalAgent|StructuredRetrievalAgent|def keyword_search' backend/agromech_api backend/tests` 只返回迁移历史或明确的兼容性说明，不返回主运行路径。
- [ ] `rg -n 'keyword.*structured.*vector|token.overlap' README.md docs` 不再把旧流程描述为当前实现。
- [ ] `git diff --check` 无输出。
- [ ] `git status --short` 只包含本计划任务明确涉及的文件。
- [ ] 后端、Worker、真实 PostgreSQL 集成测试、前端测试和前端构建结果均已记录。
- [ ] Retrieval Trace 中可看到 Query Rewrite、Dense、BM25、RRF、Rerank 和最终 Citation 状态。
- [ ] 未授权文档和显式错误机型文档不会进入最终证据。
- [ ] 证据不足时不会调用 Answer Generator 生成确定性答案。
