# Dense + BM25 + RRF 检索改造设计

## 1. 摘要

AgroMech 将直接替换当前的加权混合检索实现，目标流水线如下：

```text
查询分析
  -> LLM Query Rewrite，失败时规则降级
  -> 权限与结构化过滤
  -> Dense 与 BM25 并行召回
  -> Reciprocal Rank Fusion（RRF）
  -> Rerank
  -> 最终证据选择
  -> Citation 构建
  -> 证据充分性检查
  -> 基于引用生成答案
```

公开接口 `POST /qa/text` 的请求和响应契约保持兼容。Graph RAG 仍不进入主问答链路。本次采用直接替换方案，不建设双版本或影子检索流水线。

## 2. 目标

- 使用真正的 PostgreSQL BM25 替换当前 token-overlap 关键词匹配。
- 使用标准 RRF 替换当前绝对分数加权融合。
- 在首次检索前执行 LLM Query Rewrite。
- Query Rewrite 必须保留农机机型、故障码和零件号等精确标识符。
- 结构化元数据继续用于权限过滤、显式条件过滤和 Rerank 特征。
- Citation 只能根据最终 Rerank 后的证据生成。
- 保持现有答案、Citation、检索 Trace 和 Agent Trace API 字段兼容。
- 保留单通道降级和证据不足时拒答的能力。

## 3. 非目标

- 不在主问答链路启用 Graph RAG 或 Neo4j。
- 不替换 pgvector 或当前文本 Embedding Provider。
- 不引入 OpenSearch 或 Elasticsearch。
- 不并行运行新旧两套检索流水线。
- 不修改前端问答响应契约。
- 不在本次实现逐 Claim 的 Citation 蕴含校验，该能力留作后续增强。

## 4. 当前方案与目标方案

| 范围 | 当前行为 | 目标行为 |
| --- | --- | --- |
| 稀疏检索 | Python 遍历全部搜索行并计算 token overlap | PostgreSQL `pg_search` BM25 |
| Dense 检索 | pgvector 余弦相似度检索 | 保留，并与 BM25 使用相同结构化过滤条件 |
| 结构化检索 | 独立召回通道 | 硬过滤条件与 Rerank 特征 |
| 融合 | 通道绝对分数加权求和 | Dense 与 BM25 两路排名 RRF |
| Rerank | 百炼 Rerank，失败时规则降级 | 保留，执行位置调整到 RRF 之后 |
| Query Rewrite | 检索后按需执行规则扩展 | 检索前 LLM 改写、实体校验和规则降级 |
| Citation | 根据最终证据生成 | 保留，并严格限定为最终 Rerank 证据 |
| Trace | 通道、候选、Rerank、最终证据 | 新增 Query Rewrite 与 Fusion 明细 |

## 5. 目标数据流

### 5.1 请求处理

1. 校验并规范化原始问题。
2. 解析原始问题中的品牌、机型、子系统、部件、故障码、零件号、文档类型、语言和版本。
3. 将问题路由到对应领域行为。
4. 调用 Query Rewrite 模型生成一个规范化检索查询。
5. 校验原始查询中的受保护标识符没有被修改或丢失。
6. 如果模型失败、超时或改写丢失标识符，使用确定性同义词规则降级。
7. 为 Dense 和 BM25 构建同一个硬过滤对象。
8. 并发执行 Dense 与 BM25 召回。
9. 通过 RRF 融合两个排名列表，并保留 Fusion Top K。
10. 对融合候选执行 Rerank，并截取最终证据。
11. 只根据最终证据构建 Citation。
12. 如果最终证据或 Citation 缺失，最多执行一次规则扩展补充检索。
13. 如果证据仍然不足，返回现有 `evidence_insufficient` 响应。
14. 否则只根据 Citation 生成答案，并执行现有安全审查。

### 5.2 默认候选数量

```text
Dense Top 50 ----+
                 +-> RRF(k=60) Top 30 -> Rerank Top 30 -> 最终证据 Top 5
BM25 Top 50 -----+
```

所有数量均通过配置提供，不在代码中硬编码。

## 6. Query Rewrite

### 6.1 模型行为

Query Rewrite 模型只生成检索查询，不回答用户问题。模型可以规范化术语并补充有用的中英文同义词，但不能增加原始问题中不存在的事实。

受保护标识符包括：

- `M7040` 等机型编号；
- `E01` 等故障码；
- `RE-12345` 等零件号；
- 用户明确指定的文档版本；
- 用户明确指定的语言和文档类型。

只有全部受保护标识符都存在且规范化值不变时，改写结果才可被接受。

### 6.2 失败处理

- 模型超时、服务错误、输出无效或标识符丢失时，使用确定性规则降级。
- 规则降级保留完整原始问题，只追加已配置的领域同义词。
- 补充检索仅使用规则扩展，不再次调用 LLM。
- 总检索轮次最多为两轮：首次模型改写检索和一次规则扩展补充检索。

## 7. 结构化过滤与适用性

结构化数据不再作为第三路 RRF 输入。

硬过滤条件：

- 文档可见性和所有者；
- 文档是否处于可检索状态；
- 软删除状态；
- 用户通过 UI 明确提供的机型、文档类型和语言；
- API 已支持的其他显式过滤条件。

Rerank 软特征：

- 从自然语言推断出的品牌和机型；
- 故障码匹配；
- 零件号匹配；
- 子系统和部件匹配；
- 来源可信度和文档适用性。

Dense 与 BM25 查询必须接收同一个硬过滤对象。候选融合后仍需再次执行权限过滤，作为 fail-closed 的纵深防御。

## 8. BM25 存储与查询设计

### 8.1 PostgreSQL 扩展

生产 PostgreSQL 必须同时提供以下扩展：

```text
vector
pg_search
```

应用发布前，PostgreSQL 镜像或托管数据库扩展策略必须确认支持这两个扩展。

### 8.2 搜索表

继续使用现有 `chunk_search_index` 作为 BM25 索引数据源。其 `search_text` 继续组合：

- 文档标题；
- Chunk 正文与摘要；
- 章节名与工作表名；
- 可搜索元数据；
- 来源定位文本。

BM25 查询通过 `document_id` 关联 `documents`，在 Top K 截断前完成权限过滤和显式文档过滤，防止不可访问或不适用文档占用候选名额。

### 8.3 分词要求

BM25 分析器必须：

- 能够切分中文文本，不能把整句中文当作单个 Token；
- 保持字母数字混合的机型、故障码和零件号完整；
- 统一英文大小写；
- 能检索 Query Rewrite 产生的中英文同义词。

### 8.4 SQLite 行为

SQLite 不支持 `pg_search`。单元测试和本地 SQLite 开发使用参考 BM25 实现，但必须实现标准 BM25 公式，不能继续使用当前 token-overlap 启发式算法。PostgreSQL 集成测试是生产行为的最终依据。

## 9. RRF 融合

RRF 使用 Dense 和 BM25 的排名，不比较两者原始分数：

```text
rrf_score(chunk) = sum(channel_weight / (rrf_k + channel_rank))
```

默认值：

- `rrf_k = 60`；
- Dense 权重为 `1.0`；
- BM25 权重为 `1.0`。

融合规则：

- 按 `chunk_id` 去重；
- 原始通道分数只用于可观测性，不参与融合；
- 记录 `dense_rank`、`bm25_rank` 和 `rrf_score`；
- RRF 分数相同时，依次按最佳单路排名和 `chunk_id` 稳定排序；
- 某个通道运行时降级后，允许单通道进入融合。

## 10. Rerank 与证据选择

现有百炼 Rerank 继续作为主要重排器，输入为 RRF Top 30，输出为最终证据顺序。

Rerank 服务失败时，确定性重排使用以下特征：

- RRF 分数；
- 显式和推断出的机型适用性；
- 故障码和零件号匹配；
- 子系统和部件匹配；
- 文本相关性；
- 来源可信度；
- 范围不确定性和不适用惩罚。

只有最终证据数量限制内的候选可以进入 Citation 构建和答案生成，默认上限为 5。

## 11. Citation 契约

现有 Citation 结构保持兼容：

```text
document_id
document_title
chunk_id 或 asset_id
source_locator
evidence_snippet
evidence_type
accessible
```

Citation 不变量：

- 最终答案使用的每条证据都必须存在 Citation；
- Citation 不能引用被 Rerank 淘汰的候选；
- 不可访问或不适用的证据不能生成 Citation；
- Answer Generator 只能接收最终 Citation；
- Citation 缺失时必须判定证据不足，不能无依据生成答案。

## 12. 组件与文件改动

### 12.1 新增模块

`backend/agromech_api/rag/retrieval/bm25.py`

- 定义 `Bm25Retriever` 接口；
- 实现 PostgreSQL `pg_search` 检索；
- 实现 SQLite 参考 BM25；
- 统一返回 `chunk_id`、排名和原始分数。

`backend/agromech_api/rag/retrieval/fusion.py`

- 实现 RRF；
- 实现稳定的平分排序；
- 生成各通道排名 Trace。

### 12.2 修改现有模块

`backend/agromech_api/rag/retrieval/query_rewrite.py`

- 增加百炼 Query Rewrite Provider；
- 增加受保护标识符校验；
- 保留确定性同义词规则作为降级实现。

`backend/agromech_api/rag/agent/graph.py`

- 将 Query Rewrite 从检索后移动到首次检索前；
- 保留一次确定性补充检索；
- 禁止重复调用 LLM Rewrite。

`backend/agromech_api/rag/retrieval/indexing.py`

- 删除 token-overlap `keyword_search()`；
- 保留 `SearchIndexer` 和 `search_text` 构建；
- 为向量检索增加共享结构化过滤条件；
- 将 SQLite BM25 行为移入 `bm25.py`。

`backend/agromech_api/rag/retrieval/hybrid.py`

- 使用 `Bm25RetrievalAgent` 替换 `KeywordRetrievalAgent`；
- 移除结构化检索召回通道；
- 删除 `CHANNEL_WEIGHTS` 和通道分数累加；
- 通过 `rrf_fuse()` 融合 Dense 与 BM25 排名；
- 将 Fusion Top K 交给 Rerank；
- 保留 fail-closed 可见性检查。

`backend/agromech_api/qa/text.py`

- 构建并注入 Query Rewrite Provider；
- 分开保存原始查询与改写查询；
- 将共享过滤条件传给检索层；
- 只根据最终 Rerank 证据构建 Citation。

`backend/agromech_api/core/config.py`

- 增加 BM25、RRF、Fusion 和 Query Rewrite 设置；
- 校验候选数量关系、权重和超时参数。

`backend/agromech_api/db/models.py` 与 Alembic 迁移

- 为检索 Trace 增加 Query Rewrite 和 Fusion JSON 字段；
- 创建 `pg_search` 扩展和 BM25 索引；
- 增加过滤与关联查询需要的 B-tree 索引。

部署文档和项目文档需要同步说明扩展安装、索引重建、健康检查和回滚流程。

## 13. 配置

```text
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

配置校验要求：

- 所有 Top K 都必须为正数；
- 最终证据数量不能超过 Rerank 和 Fusion 上限；
- Fusion 上限不能超过两个召回通道候选数量之和；
- RRF 权重不能为负，并且不能同时为零；
- Query Rewrite 超时必须为正数。

## 14. Trace 与可观测性

`retrieval_logs` 新增：

- `query_rewrite`：原始查询、改写查询、模型、受保护标识符、校验结果、降级原因和耗时；
- `fusion`：RRF 配置、通道结果数量、候选通道排名、RRF 分数和耗时。

现有通道、候选、Rerank 和最终证据字段继续保留。敏感值和内部路径继续经过现有 Trace 脱敏逻辑。

运行指标包括：

- Query Rewrite 延迟与降级率；
- BM25 与 Dense 延迟、结果数量和降级率；
- Dense/BM25 在 K 范围内的重合度；
- RRF 与 Rerank 延迟；
- 证据与 Citation 数量；
- 证据不足比例；
- 不包含答案生成的端到端检索 P95。

## 15. 失败与降级策略

| 故障 | 必须执行的行为 |
| --- | --- |
| Query Rewrite 超时或服务错误 | 使用确定性规则降级 |
| Query Rewrite 丢失受保护标识符 | 拒绝改写并使用规则降级 |
| Dense 运行时失败 | 仅使用 BM25，并记录 `dense_degraded` |
| BM25 运行时失败 | 仅使用 Dense，并记录 `bm25_degraded` |
| 两个召回通道均失败或均无证据 | 返回证据不足响应 |
| Rerank 服务失败 | 使用确定性 Rerank |
| Citation 构建后为空 | 拒绝生成有依据的答案 |
| 启动时缺少数据库扩展或索引 | Readiness 失败，不接收 QA 流量 |

所有降级模式都必须执行权限过滤。

## 16. 测试策略

### 16.1 单元测试

- 参考 BM25 分数和排序；
- 中文分词和标识符保留；
- RRF 公式、去重、单通道输入和稳定平分排序；
- Query Rewrite 校验和确定性降级；
- 硬过滤和结构化 Rerank 特征；
- Citation 与最终证据的一一对应；
- 配置校验。

### 16.2 PostgreSQL 集成测试

- 迁移能创建 `vector`、`pg_search` 和必要索引；
- BM25 在固定语料上返回预期排名；
- pgvector Dense 检索与 BM25 使用相同过滤条件；
- 可见性和所有者过滤在 Top K 截断前发生；
- RRF 正确记录两路排名；
- 索引重建能重新填充可搜索数据；
- 运行时通道降级会写入 Trace。

### 16.3 端到端测试

- 包含机型、故障码、零件号和保养问题的文本问答；
- 匿名访问与私有文档隔离；
- Query Rewrite 成功、无效结果、超时和降级；
- Dense-only 与 BM25-only 降级响应；
- Rerank 降级；
- 证据不足；
- Citation 和 Trace 响应兼容性。

## 17. 验收标准

- Query Rewrite 评估集中的受保护标识符保留率为 100%。
- 最终证据中不可访问文档数量为 0。
- 用户显式过滤机型时，最终证据中错误机型文档数量为 0。
- 每条最终证据都有有效 Citation，且 Citation 不引用被 Rerank 淘汰的候选。
- `Recall@20` 和 `nDCG@10` 不低于当前基线，并且至少一项提升。
- 不包含答案生成的检索 P95 不超过当前基线的 1.5 倍。
- Query Rewrite、Dense、BM25、RRF、Rerank 和 Citation 状态都可以通过检索 Trace 查看。
- 现有问答 API 响应契约保持兼容。

## 18. 直接替换发布计划

1. 记录当前检索质量和延迟基线。
2. 备份生产数据库。
3. 安装同时提供 `pgvector` 和 `pg_search` 的 PostgreSQL 构建。
4. 执行只新增对象的 Alembic 迁移。
5. 重建搜索索引和向量索引。
6. 在生产数据库执行 Dense、BM25、RRF、权限和 Citation 冒烟测试。
7. 发布新的应用实现。
8. 执行文本问答和 Trace 冒烟测试。
9. 监控通道降级、证据不足、Citation 和延迟指标。

数据库迁移保持增量性质，不立即删除当前搜索表或字段。如果应用验证失败，重新部署上一版本应用镜像，并保留新增扩展、索引和 Trace 字段；回滚不需要执行破坏性数据库降级。

## 19. 关键决策与权衡

- PostgreSQL BM25 保持单数据库架构，但要求数据库镜像或服务商支持 `pg_search`。
- 直接替换减少临时代码复杂度，但要求协调数据库和应用发布。
- RRF 避免比较不可直接对齐的 Dense 与 BM25 原始分数，但融合阶段不使用分数幅度信息。
- 结构化过滤保护机型适用性，但依赖元数据质量，因此只有显式高置信度条件作为硬过滤。
- 检索前 LLM Rewrite 增加一次模型调用，实体校验和确定性降级用于控制风险。
- 数据库保持增量修改，使新应用不包含旧检索流水线时仍可通过上一镜像回滚。

## 20. 后续评估点

当语料规模、流量或质量数据证明有必要时，再评估：

- 可学习或随查询变化的 RRF 权重；
- 多查询召回，而不是单个规范化查询；
- 逐 Claim Citation 蕴含校验；
- 外部搜索基础设施；
- 将 Graph RAG 作为独立证据扩展阶段；
- 面向高查询量的近似 BM25 或缓存。
