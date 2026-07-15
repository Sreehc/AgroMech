# Task 7 实施与审查返修报告

## 状态

已完成 Query Rewrite 前移、最多两轮检索、最终 Citation 约束，以及审查要求的 Trace 冲突保护、Dense-only 阈值和视觉证据闭环。Task 7 当前聚焦测试全部通过；完整 backend 仅保留一个已明确交由 Task 8 迁移的旧 Retrieval Trace 断言失败。

## 初始实施

- Agent 状态新增 `query_rewrite`、`rewritten_query` 和 `retrieval_round`，并将 `rewrite_fn` 注入 `AgentController` 与 LangGraph。
- 主链顺序改为 `parse -> route -> rewrite -> retrieve -> planner -> evidence_check`。
- 首轮 Rewrite 使用 `supplemental=False`；仅可能发生一次的次轮 Rewrite 使用 `supplemental=True`，因此 LLM Provider 最多调用一次，次轮为规则补写。
- 改写后的查询只用于文本与视觉召回；原始问题继续用于规划、证据检查、答案生成和 Trace 的 `query`。
- `original_question`、`query_rewrite`、`retrieval_round` 已贯穿 RetrievalAgent、LangChain Tool、Adapter 和 QA 检索边界。
- QA 检索边界统一构造一个 `RetrievalFilters`，包含请求过滤条件和 viewer 可见性范围。
- 最终组装先移除 `not_applicable`，再应用 `final_evidence_limit`，分别生成文本和视觉 Citation，并按最终证据顺序恢复；任何证据无法映射 Citation 时返回证据不足。
- Citation Trace 只在 `answer_text_question()` 获得最终响应后写入，成功、证据不足和视觉合并都记录实际返回的 Citation。
- 主文本 QA 链未启用 Graph RAG/Neo4j。

## 授权前移的多轮 Trace

Task 8 的多轮 Trace upsert 合同经协调授权前移到 Task 7，否则同一 `trace_id` 的次轮真实检索会触发唯一键冲突。

- `query_rewrite` 与 `fusion` 维护有序 `attempts`，并用当前轮替换 `final`。
- `fusion.retrieval_duration_ms` 累加两轮检索耗时。
- `channels`、`model_config`、`candidates`、`rerank` 和 `final_evidence` 反映最终检索轮。
- 同一 `trace_id` 始终只保留一条 `retrieval_logs` 记录。
- Task 8 的角色可见性与 Trace 脱敏未在本任务实现。

## 审查返修接管状态

接管时 HEAD 为 `8793cbf feat: rewrite queries before retrieval`，工作树已有前一代理留下的 12 个文件未提交差异。未执行 reset/revert，所有返修都在现有差异上核验和补齐。

接管后的最小回归：

```text
9 passed, 1 warning
```

接管后的 Task 7/Agent/Hybrid/filters 基线：

```text
139 passed, 1 warning
```

## 审查项关闭情况

### 1. Trace 审计与原子冲突

- `retrieval_round` 已从 Graph 状态贯穿 Agent、Tool、Adapter、QA 边界到 `write_retrieval_log()`；仅允许 1 或 2。
- round 1 使用单条原子 `INSERT`。唯一 `trace_id` 冲突转换为 `RetrievalTraceConflictError`，QA API 明确返回 HTTP 409 / `trace_id_conflict`，不会覆盖旧行。
- round 2 在事务内锁定现有行，只接受：原始 query 相同、filters 相同、仅一轮 attempts、尚无 Citation、最终轮为 1 的记录。
- round 2 使用带 JSON 轮次条件的 `UPDATE`；`rowcount != 1` 作为原子守卫冲突处理，异常回滚整个事务。
- 新增 query 不匹配、filters 不匹配、已完成 Citation、重复 round 2、零行原子更新五类回归，并逐项验证原审计快照不变。
- 两个独立 POST 复用 `X-Trace-Id` 的端到端测试验证：第二次返回 409，首条记录的 query、filters、rewrite、fusion、candidates、rerank、final evidence 和 Citation channels 全部不变。
- 使用真实 ParadeDB/PostgreSQL 0.24.2 验证唯一键冲突、JSON 条件更新和 attempts `[1, 2]`，测试数据随后删除。

### 2. Dense-only 相似度阈值

- 新增 `dense_only_min_similarity` 配置，默认 `0.25`，校验为有限的 `[0, 1]` 概率值，并写入 Trace `model_config`。
- Dense 和 BM25 两路收集完成后再过滤：低于阈值且仅由 Dense 命中的 chunk 被移除；同 chunk 若有 BM25 命中，保留 Dense channel 参与 RRF。
- 竞争候选测试覆盖：高分 Dense-only 候选、低分 Dense+BM25 候选和低分无关 Dense-only 候选同时存在时，融合候选保留双通道并正确排序。

### 3. 视觉证据补强

- 移除 `retrieval.status == evidence_insufficient` 对最终答案组装的过早短路。
- Planner 的 `need_visual` 不再因文本证据充分而被提前清除；证据充分性以文本与视觉合并后的 `final_evidence` 和 1:1 Citation 为准。
- 默认 Planner + 真实 QA 组装测试覆盖 visual-only 和 text+visual，且验证响应 Citation 顺序、最终 Trace evidence 顺序和 Citation channel 一致。

### 4. 端到端两轮闭环

- 真实 POST 覆盖首轮不足、规则次轮成功，验证 Provider 仅调用一次、原始 query 不变、attempts 为 `[1, 2]`、最终 Citation Trace 为 `ok`。
- 真实 POST 覆盖两轮仍不足，验证空 Citation、attempts 为 `[1, 2]` 和 Citation Trace 为 `insufficient`。
- 真实 POST 覆盖重复客户端 Trace Header 的 409 与旧审计不变。

### 5. 原 Task 7 约束

- Query Rewrite 位于首次检索前；最多两轮；LLM 仅首轮可用。
- 原始问题与 Rewrite Trace 不在适配层丢失。
- 最终 Citation 与合并后的 final evidence 按顺序 1:1 组装。
- POST 请求字段保持兼容；Graph/Neo4j 继续禁用。

## TDD 证据

初始 Task 7 RED：

```text
.venv/bin/python -m pytest backend/tests/test_agent_controller.py backend/tests/test_langchain_adapters.py backend/tests/test_text_qa.py -q
19 failed, 16 passed
```

多轮 Trace RED：

```text
1 failed: UNIQUE constraint failed: retrieval_logs.trace_id
```

召回专用 Rewrite RED：

```text
1 failed: planner received rewritten query instead of original question
```

Dense 相关性 RED：

```text
2 failed, 1 passed
```

审查返修新增的 round 2 拒绝测试在现有返修实现上直接通过，说明前一代理留下的条件锁定实现已覆盖这些合同；未为了制造 RED 而回退已存在的生产修复。

## 最终验证

聚焦测试：

```text
.venv/bin/python -m pytest backend/tests/test_agent_controller.py backend/tests/test_agent_agents.py backend/tests/test_langchain_adapters.py backend/tests/test_text_qa.py backend/tests/test_query_rewrite.py backend/tests/test_hybrid_retrieval.py backend/tests/test_retrieval_filters.py -q
144 passed, 1 warning
```

完整 backend：

```text
.venv/bin/python -m pytest backend/tests -q
474 passed, 3 skipped, 1 failed, 6 warnings
```

唯一失败：`backend/tests/test_retrieval_trace.py::test_retrieve_with_trace_logs_query_filters_channels_rerank_and_final_evidence`。该旧断言要求 `hybrid_retrieve_with_trace()` 从查询文本自动推断 `filters.model`；Task 6 合同要求调用方显式传入共享 `RetrievalFilters`，已确定由 Task 8 迁移。没有其他 backend 失败。

静态检查：

- 变更文件 `compileall`：通过。
- `git diff --check`：通过。
- 仓库与虚拟环境没有配置/安装 Ruff、Flake8、Black、Mypy 或 Pylint，因此无可用项目 Python lint 命令。

## 变更文件

- 配置与错误：`core/config.py`、`core/errors.py`。
- QA 与 Agent 透传：`qa/text.py`、`rag/agent/agents/retrieval.py`、`rag/agent/tools.py`、`rag/langchain/adapters.py`。
- 检索与 Trace：`rag/retrieval/hybrid.py`。
- 测试：`test_agent_agents.py`、`test_hybrid_retrieval.py`、`test_langchain_adapters.py`、`test_retrieval_filters.py`、`test_text_qa.py`。

## 自审

- 确认 Controller 最多发起两次检索，Provider-capable Rewrite 最多一次。
- 确认原始问题用于日志、规划、证据检查和答案语义，改写文本只用于召回。
- 确认 round 1 不会 upsert 覆盖旧 Trace，round 2 不会创建缺失记录或覆盖已完成/不匹配/两轮完成记录。
- 确认 Dense 阈值只淘汰 low-similarity dense-only hit，不会移除 BM25 共命中 chunk 的 Dense RRF 贡献。
- 确认视觉证据可独立补足文本不足，也可与文本证据按最终顺序共同生成 Citation。
- 确认最终 Citation Trace 不在逐轮检索或答案 helper 内提前写入。
- 确认主文本 QA 路径未初始化 Graph RAG/Neo4j。

## 疑虑与后续边界

- Task 8 仍需迁移唯一旧 Retrieval Trace 断言，并实现角色级 Trace 暴露和脱敏。
- Dense 阈值基于原始 cosine 分数；若后续替换 embedding 模型，需要重新校准 `0.25`。
- Starlette TestClient 与 SWIG 的第三方弃用警告仍存在，与本任务无关。

## 最终复审第二轮返修

### 接收的 Important

1. 视觉 evidence 已可进入最终答案，但视觉召回只检查 public/owner，没有完整复用文本检索的状态、删除、显式字段和 subsystem 过滤，也没有合并后的 fail-closed 校验。
2. Planner 标记 `need_visual=True` 时，EvidenceReviewer 仍会仅凭文本 evidence/citation 判定 sufficient；最终简单截取前 N 条还可能把视觉 evidence 全部裁掉。

本轮保留 `ef4db60`，未 reset/revert，在其上新增独立修复提交。

### TDD RED

首组五条回归：

```text
.venv/bin/python -m pytest \
  backend/tests/test_search_indexing.py::test_visual_page_search_returns_pgvector_refs \
  backend/tests/test_search_indexing.py::test_visual_page_search_uses_pgvector_distance_operator_for_postgres \
  backend/tests/test_agent_controller.py::test_agent_controller_rejects_text_only_evidence_when_visual_is_required \
  backend/tests/test_text_qa.py::test_text_qa_real_visual_retrieval_filters_forbidden_documents_before_citation \
  backend/tests/test_text_qa.py::test_text_qa_final_limit_reserves_visual_evidence_slot -q
5 failed
```

失败分别证明：视觉搜索未接收 `RetrievalFilters`；空视觉只调用一轮且仍进入答案；deleted/processing/他人 private/model mismatch 进入 Citation；文本占满 limit 时视觉被裁掉。

合并后二次校验 RED：

```text
.venv/bin/python -m pytest \
  backend/tests/test_text_qa.py::test_text_qa_final_assembly_rejects_private_visual_provider_evidence -q
1 failed
```

该测试直接把他人 private asset 注入最终 evidence，旧组装仍生成文本答案，证明仅依赖首次视觉搜索不足以 fail closed。

### 视觉过滤 SQL 语义

- `visual_page_search()` 现在强制接收一个不可变 `RetrievalFilters`；同一个实例同时用于召回 SQL 与召回后的校验。
- SQLite 和 PostgreSQL 视觉 SQL 均在排序/`LIMIT` 前执行：
  - `documents.status = 'indexed'`；
  - `documents.deleted_at IS NULL`；
  - public 或当前 viewer 拥有的文档；
  - `brand`、`model`、`document_type`、`language`、`document_version` 的文档列精确匹配；
  - `subsystem` 使用相关 `EXISTS`：asset 所属 document 必须存在一个 chunk，且该 chunk 的 `chunk_entity_links` 同属该 document、`entity_type='system'`、`normalized_value` 精确匹配。没有实体链接时自然返回 false，fail closed。
- 视觉 embedding 与 asset 的 join 同时校验 `asset_id` 和 `document_id`，并要求 asset 类型为 `page_image`，防止跨文档伪造引用。
- 新增真实 `limit=1` 竞争测试：更高分的 processing 视觉向量必须在 Top K 前被排除，较低分的 indexed + subsystem 匹配页面仍返回。
- 新增真实 QA POST：deleted、processing、他人 private、model mismatch 四个高分页面均具有匹配 subsystem chunk，但不能进入最终 evidence/Citation；仅允许页面返回。
- 使用真实 ParadeDB/PostgreSQL 0.24.2 执行包含全部文档字段与 subsystem `EXISTS` 的视觉向量 SQL，返回允许 asset；临时数据随后删除。

### 合并后二次校验

- `retrieve_visual_for_text_agent()` 在视觉 Provider 返回后按真实 `(asset_id, document_id)` 再查 `document_assets + documents`，复用完整过滤条件。
- `answer_for_text_agent()` 在文本/视觉合并后、Citation 前再次执行相同校验；不存在、跨文档、已删除、非 indexed、无权限或显式过滤不匹配的视觉 evidence 被移除。
- 如果 Planner 要求视觉，但二次校验后没有有效 asset，最终 evidence 清空并返回 `evidence_insufficient`，不会退化为纯文本确定性答案。

### 视觉模态不变量与选择顺序

- EvidenceReviewer 在 `planner.need_visual=True` 时要求 evidence 与 Citation 至少有一个完全相同的 `(asset_id, document_id)`；只有文本或 asset/citation 不匹配时保持 insufficient。
- AnswerWriter 只接受 EvidenceReviewer 的 `sufficient`，不再因存在任意 `visual_page` 字段绕过 generation guard。
- 空视觉最多按既定上限执行两轮，随后返回空 Citation 的 evidence insufficient；答案函数不会执行。
- 最终 evidence 的确定性选择规则：
  1. 按原合并顺序取前 `final_evidence_limit`；
  2. 若视觉为必需且已选集合含有效视觉，保持不变；
  3. 若视觉为必需、后续存在有效视觉但前 N 条全是文本，则保留前 N-1 条文本，并用第一个有效视觉替换最后一条；
  4. `limit=1` 时选择该视觉；没有有效视觉则 fail closed。
- 上传图片问答已有 `image_context` 时，不再把 `text_visual` 路由误解为还需资料页视觉 Citation；上传图像负责交互模态，检索到的文本资料仍负责来源 Citation。现有图片问答测试全部恢复通过。

### GREEN 与最终验证

新增核心回归：

```text
5 passed, 1 warning
3 passed, 1 warning
4 passed, 1 warning
```

Task 7、Search Indexing 与 Image QA 扩展聚焦：

```text
.venv/bin/python -m pytest \
  backend/tests/test_agent_controller.py backend/tests/test_agent_agents.py \
  backend/tests/test_langchain_adapters.py backend/tests/test_text_qa.py \
  backend/tests/test_query_rewrite.py backend/tests/test_hybrid_retrieval.py \
  backend/tests/test_retrieval_filters.py backend/tests/test_search_indexing.py \
  backend/tests/test_image_qa.py -q
174 passed, 1 warning
```

完整 backend：

```text
.venv/bin/python -m pytest backend/tests -q
479 passed, 3 skipped, 1 failed, 6 warnings
```

唯一失败仍是 Task 8 过渡项：`test_retrieve_with_trace_logs_query_filters_channels_rerank_and_final_evidence` 继续要求从 query 隐式推断 `filters.model`。

静态验证：变更文件 `compileall` 与 `git diff --check` 通过；仓库和虚拟环境仍无可用 Python lint 工具。

### 本轮自审

- 确认视觉过滤发生在 SQLite 本地评分 Top K 和 PostgreSQL `ORDER BY distance LIMIT` 之前。

## 最终审查 CAS 返修

### 问题与处理

最终审查指出，第二轮检索原先只在 Python 预检查 Citation，实际 `UPDATE` 只约束 JSON 内的最终轮次。SQLite 忽略 `FOR UPDATE`，因此 Citation 若恰好在预检查和更新之间落库，第二轮会用旧 `channels` 覆盖 Citation 审计。

新增 `retrieval_logs.retrieval_round` 与 `retrieval_logs.citation_status` 两个非空状态字段，并通过 `0014_trace_cas_fields` 迁移部署。首轮写入 `(1, pending)`；Citation 记录成功后原子转为 `completed`；第二轮使用单条、可移植的普通列 CAS：

```sql
UPDATE retrieval_logs
SET retrieval_round = 2, ...
WHERE id = :id
  AND retrieval_round = 1
  AND citation_status = 'pending'
```

SQLite 与 PostgreSQL 由 SQLAlchemy 编译出相同的条件 SQL，不依赖 JSON 缺失、`null` 或 JSON 运算符在两种方言上的细微差异。Python 预检查继续验证原始 query、filters 和 attempts 形态；数据库 CAS 则保证并发时只有一个第二轮或 Citation 状态转换能提交。`rowcount != 1` 仍会抛出既有 `RetrievalTraceConflictError`，事务回滚，不会覆盖 Citation。

`record_citation_trace()` 同样按读取到的 `id`、`retrieval_round` 和 `citation_status='pending'` 条件更新，避免它的旧快照覆盖已经推进的检索轮次。

### TDD

RED：新增 SQLite 时序回归，通过 `before_cursor_execute` 钩子在第二轮条件 `UPDATE` 即将执行时调用真实 `record_citation_trace()`；旧实现在此测试中不抛冲突：

```text
1 failed: DID NOT RAISE RetrievalTraceConflictError
```

GREEN：引入显式 CAS 字段与迁移后，测试稳定通过。它断言第二轮返回既有安全冲突语义，Citation 保留为 `ok`，且 query、filters、query_rewrite、fusion、candidates、rerank、final_evidence 与原有 channels 均未变化。

### 验证

```text
backend/tests/test_hybrid_retrieval.py::test_supplemental_round_does_not_overwrite_citation_written_before_cas_update
1 passed

backend/tests/test_migrations.py backend/tests/test_data_model.py backend/tests/test_hybrid_retrieval.py
52 passed

真实 ParadeDB/PostgreSQL 0.24.2：迁移 0014 成功，retrieval_logs 两个 CAS 字段存在。
scripts/test-integration.sh
164 passed, 1 warning

完整 backend
480 passed, 3 skipped, 1 failed, 6 warnings
```

完整 backend 的唯一失败仍为已记录的 Task 8 过渡断言：`backend/tests/test_retrieval_trace.py::test_retrieve_with_trace_logs_query_filters_channels_rerank_and_final_evidence` 仍要求从 query 隐式推断 `filters.model`；本次未修改该合同。

`compileall` 与 `git diff --check` 均通过。

### 自审与剩余风险

- 第二轮的并发完整性不再依赖 SQLite 不支持的行锁，也不依赖 JSON 的 missing/null 解释。
- 历史迁移前已有记录默认标记为 `pending`；它们若已有 Citation，现有 Python 预检查仍拒绝第二轮。新的 Citation 写入会将状态推进为 `completed`。
- `record_citation_trace()` 在其自身 CAS 因并发状态变化失效时保持无副作用；该路径不应发生于正常单请求 QA 编排，异常跨请求复用同一 trace 时不会以旧 `channels` 覆盖新审计。
- 确认 subsystem 不做 OCR/标题/字符串猜测，只依赖结构化 chunk entity link。
- 确认同一个视觉过滤对象用于搜索与首次防御，最终答案边界用相同 viewer/request 值重建等价不可变对象并再次检查。
- 确认假 Provider 返回不存在或无权限 asset 时无法生成视觉 Citation。
- 确认文本满额时只替换最后一个文本，前序文本顺序稳定，evidence 与 Citation 仍为 1:1。
- 确认 visual-only、text+visual、上传图片问答、Dense/BM25 过滤和 Trace 冲突测试未回归。
- 确认未加入 Graph RAG/Neo4j。

### Residual Risk（Minor，仅记录）

SQLite 不支持 PostgreSQL 等价的行级 `SELECT ... FOR UPDATE`。round 2 当前依赖 JSON 轮次条件与 `rowcount` 做有限 CAS；若要把 query、filters、Citation 完成状态也纳入 SQLite 的完整原子 CAS，需要新增显式 attempt/version 列及 schema migration。本轮按复审要求不做迁移；生产 PostgreSQL 路径仍由行锁保护完整前置条件。

## 接管自审与提交

- 接管时基线为 `ef4db60`；未提交差异仅覆盖 Task 7 的视觉检索、Citation 不变量及其回归测试，`git diff --check` 通过。
- 复核确认：结构化视觉过滤位于 SQLite 评分截断及 PostgreSQL `ORDER BY ... LIMIT` 之前；合并后以 `(asset_id, document_id)` 防御性复验；`need_visual` 同时要求有效 asset evidence 与对应 Citation；文本占满限额时保留一个可验证视觉 evidence；上传图像上下文不再触发资料页视觉强制要求。
- 接管验证：`174 passed, 1 warning`（Task 7、索引、检索、图片 QA 扩展聚焦）；变更文件 `compileall` 与 `git diff --check` 通过。
- 提交：`a11c74c fix: secure visual evidence retrieval`。
