# Agentic RAG with RabbitMQ and LangGraph Design

## Goal

Complete the "Agentic 多模态 RAG 落地计划" in a production-controlled way:

1. Finish the upload/write pipeline first with RabbitMQ-backed task dispatch.
2. Build the Agentic question-answering/read pipeline after multimodal evidence is reliably indexed.
3. Use LangGraph for QA orchestration, while preserving existing retrieval, citation, storage, OCR, graph, and model adapters.

The guiding strategy remains:

```text
Text-first, Visual-on-demand
```

## Current State

The project already has most first-stage RAG capabilities:

- Upload API creates `documents` and `ingest_tasks`.
- `IngestTaskRunner` manages queued, processing, succeeded, failed, dead, reprocessing, deleting states.
- Worker processing can parse text/table/image/PDF, run OCR/vision, extract entities, sync graph data, and index chunks.
- PaddleOCR cloud client and OCR ingestion exist.
- Text QA and image QA currently call existing retrieval and answer-generation functions directly.
- LangChain and LangGraph are not currently dependencies.

The main gaps from the Agentic plan are:

- RabbitMQ producer/consumer layer for ingestion task dispatch.
- Agent Controller abstraction.
- Tool routing layer.
- Evidence sufficiency loop.
- Query rewrite loop.
- Generation guard and agent decision trace.

## Architecture Decision

Use different orchestration mechanisms for write and read paths:

```text
Upload/write path: deterministic worker pipeline + RabbitMQ + DB state machine
QA/read path: LangGraph workflow + existing RAG services
```

RabbitMQ should not replace `ingest_tasks`. The database remains the source of truth for task state, retry count, failure stage, audit trail, and document status. RabbitMQ is the wake-up and distribution mechanism.

LangGraph should not replace the existing RAG implementation. It should orchestrate the existing functions as graph nodes.

LangChain should be used narrowly for tool/model/structured-output conveniences where it reduces glue code. It should not become a broad rewrite of retrieval, vector storage, graph retrieval, citation building, or answer generation.

## Phase 0: RabbitMQ Upload Pipeline

### Flow

```text
POST /documents
  -> validate / deduplicate / store file
  -> insert documents row
  -> insert ingest_tasks row with status=queued
  -> publish RabbitMQ message
  -> RabbitMQ worker receives message
  -> worker calls IngestTaskRunner.run_next(process_ingest_task)
  -> task processor runs parse/OCR/vision/entity/graph/index stages
  -> DB state becomes indexed / failed / dead
  -> worker ack/nack follows DB outcome
```

The same queue publication pattern applies to reprocess and delete tasks.

### Components

`backend/agromech_api/config.py`

- Add RabbitMQ settings:
  - `rabbitmq_url`
  - `rabbitmq_queue`
  - `rabbitmq_exchange`
  - `rabbitmq_routing_key`
  - `rabbitmq_publish_enabled`
  - `rabbitmq_consume_prefetch`
  - `rabbitmq_reconnect_seconds`

`backend/agromech_api/task_queue.py`

- Define a small publisher abstraction.
- Provide a RabbitMQ publisher implementation using `pika`.
- Provide a no-op/local publisher for tests or disabled mode.
- Publish only task identifiers and task type, not full document content.

Message shape:

```json
{
  "task_id": "uuid",
  "document_id": "uuid",
  "task_type": "ingest|reprocess|delete",
  "attempt": 0,
  "created_at": "ISO-8601"
}
```

`backend/agromech_api/documents.py`

- After the DB transaction creates a task, publish a RabbitMQ message.
- If publishing fails, keep the DB task queued and return a readable error only when RabbitMQ is configured as required.
- In disabled/local mode, keep existing DB-backed behavior.

`worker/agromech_worker/rabbitmq.py`

- Add a long-running consumer.
- Consume messages from the configured queue.
- For each message, call `IngestTaskRunner.run_next(process_ingest_task)`.
- Ack when the runner completes with `succeeded`, `failed`, `dead`, or `idle`.
- Treat `idle` as an idempotent duplicate/stale-message outcome: no queued DB task exists, so the message should not be retried forever.
- Nack/requeue only for infrastructure-level failures before the DB state transition is reliable.
- Use prefetch to respect worker concurrency.

`worker/agromech_worker/main.py`

- Keep `run_once()` for tests and manual execution.
- Add a consume mode for production.
- Support graceful shutdown.

### Reliability Rules

- DB task state is authoritative.
- RabbitMQ message is a delivery signal, not the task record.
- Worker must tolerate duplicate messages by relying on `IngestTaskRunner` to pick the next queued task.
- If RabbitMQ publish fails after DB commit, the queued DB task remains recoverable by `run_once()` or a future scanner.
- Message ack should happen only after DB state has been advanced.
- Secrets and connection errors must not expose credentials in logs or API responses.

### Tests

- Upload creates DB task and publishes message.
- Reprocess and delete publish messages.
- Publisher can be disabled for local/test mode.
- Consumer acks successful processing.
- Consumer handles failed/dead DB outcomes without losing audit state.
- Consumer handles malformed messages safely.
- Existing `run_once()` tests continue passing.

## Phase 1: LangGraph Agent Controller Minimum Loop

### Flow

```text
/qa/text or /qa/image
  -> AgentController.invoke()
  -> LangGraph workflow
  -> parse_query_node
  -> route_node
  -> retrieve_node
  -> citation/answer path
  -> return existing API-compatible response
```

### Components

`backend/agromech_api/agent_state.py`

- Define the graph state:
  - question
  - filters
  - image context when present
  - parsed query
  - route decision
  - retrieval rounds
  - final evidence
  - citations
  - safety warnings
  - uncertainty
  - agent trace
  - answer payload

`backend/agromech_api/agent_router.py`

- Rule-first routing:
  - maintenance period, parameter, fault code, model-specific text questions -> text only
  - image/page/diagram/location/table visual wording or uploaded image -> text plus visual
- LLM routing is only used when rules are ambiguous.
- LLM output must be structured and testable.

`backend/agromech_api/agent_graph.py`

- Build the LangGraph workflow.
- Encode conditional edges for route decisions.
- Keep max round count in state/config.

`backend/agromech_api/agent_controller.py`

- Public orchestration boundary used by `text_qa.py` and `image_qa.py`.
- Converts current request data into `AgentState`.
- Invokes the graph.
- Converts graph output back into the existing API response shape.

### Compatibility

- `/qa/text` and `/qa/image` request/response contracts remain compatible.
- Existing authorization/session behavior remains in the route modules.
- Existing `answer_generation`, `hybrid_retrieve_with_trace`, `parse_query`, and citation builders remain the underlying implementation.

### Tests

- Text-only maintenance questions do not trigger visual retrieval.
- Visual/image questions trigger visual-aware routing.
- Current text QA tests continue to pass.
- Current image QA tests continue to pass.
- Agent trace records route, reason, tools used, and retrieval rounds.

## Phase 2: Evidence Check and Retrieval Loop

### Flow

```text
retrieve
  -> evidence_check_node
  -> sufficient -> generation guard
  -> insufficient and attempts < 2 -> rewrite or add visual -> retrieve again
  -> insufficient and attempts >= 2 -> insufficient answer
```

### Components

`backend/agromech_api/evidence_check.py`

- Rule-first evidence sufficiency:
  - required slots covered: model, subsystem, part, fault code, maintenance period when applicable
  - minimum evidence count
  - top evidence consistency
  - citation availability
- LLM check only when rule result is ambiguous.
- Output must be structured:

```json
{
  "status": "sufficient|insufficient|ambiguous",
  "missing": ["model", "part"],
  "reason": "top evidence does not cover the requested model",
  "confidence": 0.81
}
```

`backend/agromech_api/query_rewrite.py`

- Rewrite only when evidence is insufficient.
- Add domain synonyms, model aliases, part aliases, and fault-expression variants.
- Preserve original user intent and filters.

Graph changes:

- Add conditional edge from evidence check to rewrite/add-visual/retrieve.
- Default max supplemental retrieval rounds is 2.
- Every round appends to agent trace.

### Tests

- Insufficient evidence triggers at most two additional retrieval rounds.
- Query rewrite preserves filters.
- If evidence remains insufficient, answer does not fabricate.
- Visual retrieval is added only when route/evidence indicates visual need.

## Phase 3: Generation Guard

### Flow

```text
final evidence
  -> draft or planned answer
  -> conclusion-evidence alignment check
  -> citation-aware answer
  -> response with agent trace
```

### Rules

- A claim that affects repair action, safety, interval, torque, fluid, part number, or diagnostic conclusion must be backed by citation.
- If required support is absent, the response downgrades to evidence-insufficient or limited answer.
- Safety warnings remain deterministic and cannot be suppressed by prompt injection.

### Tests

- Unsupported conclusions are removed or cause evidence-insufficient response.
- High-risk repair answers keep safety reminders.
- Prompt injection cannot disable citation or safety requirements.
- Agent trace is available without leaking secrets or sensitive internal paths.

## Dependency Plan

Add Python dependencies only when Phase 1 starts:

```toml
langchain-core
langgraph
```

Add `langchain` only if concrete code requires its higher-level abstractions. Prefer `langchain-core` plus `langgraph` first.

Add RabbitMQ dependency in Phase 0:

```toml
pika
```

## Rollout Plan

1. Implement RabbitMQ settings, publisher, and tests with publishing disabled by default in tests.
2. Wire document/reprocess/delete task publication.
3. Implement RabbitMQ worker consumer while preserving `run_once()`.
4. Verify upload-to-indexed flow through RabbitMQ.
5. Add LangGraph dependencies and build the minimum Agent Controller graph.
6. Route `/qa/text` and `/qa/image` through the controller while preserving API compatibility.
7. Add evidence check, query rewrite, and max two-round retrieval loop.
8. Add generation guard and agent trace response details.

## Non-Goals

- Do not replace Zvec with a LangChain vector store.
- Do not replace Neo4j graph retrieval with a LangChain graph abstraction.
- Do not use a prebuilt ReAct agent as the main QA workflow.
- Do not let the LLM freely choose arbitrary tools.
- Do not change public API contracts for `/qa/text` or `/qa/image`.
- Do not use LangGraph for the upload ETL pipeline in this iteration.

## Open Revisit Points

- Add LangGraph checkpoint persistence after the minimum workflow is stable.
- Add LangSmith tracing if the team wants external observability.
- Add a DB-backed scanner that republishes stale queued tasks if RabbitMQ publish fails after DB commit.
- Promote agent trace into a first-class retrieval trace section if frontend debugging needs it.
