# 多 Agent 改造路线

## 1. 背景和目标

当前 AgroMech 问答主链路是单 `AgentController` + LangGraph 工作流。它已经把解析、路由、检索、证据检查、query rewrite 和回答生成组织在固定流程中，但这些能力目前主要表现为 graph 节点和函数调用，而不是明确的 Agent 边界。

本改造的目标不是把系统改成多个自由对话 Agent，而是演进为受控多 Agent RAG：

- 保持 `/qa/text` 和 `/qa/image` API 兼容。
- 保持前端、资料库、导入 worker 和 session 流程不受影响。
- 把现有 LangGraph 节点拆成可测试、可追踪的 Agent class。
- 增强证据审查、安全审查、领域分流和检索并行能力。
- 为未来真正的 A2A 通信预留契约，但第一阶段不引入网络级 A2A 协议。

## 2. 总体架构方向

现状：

```text
AgentController
  -> LangGraph:
     parse -> route -> retrieve -> planner -> evidence_check -> rewrite -> answer
```

目标形态：

```text
AgentController
  -> QueryAnalystAgent
  -> RouterAgent
  -> RetrievalAgent
  -> EvidenceReviewerAgent
  -> Domain Specialist Agent
  -> QueryRewriteAgent
  -> AnswerWriterAgent
  -> SafetyReviewerAgent
```

这些 Agent 初期都运行在同一个 FastAPI 进程内，通过 LangGraph state 传递上下文。每个 Agent 只负责一个明确决策边界，并输出结构化结果和 trace。

## 3. 通信契约

第一阶段不实现完整 A2A 网络协议。系统先定义进程内 Agent 契约，做到 A2A-ready：

```python
class AgentResult(TypedDict):
    status: str
    output: dict[str, Any]
    trace: dict[str, Any]
```

建议所有 Agent 遵守统一接口：

```python
class BaseAgent(Protocol):
    name: str

    def run(self, state: AgentState) -> AgentResult:
        ...
```

统一 trace 字段建议包括：

```json
{
  "agent": "EvidenceReviewerAgent",
  "step": "evidence_review",
  "status": "sufficient",
  "reason": "evidence covers model and fault code",
  "confidence": 0.84
}
```

这个契约后续可以自然升级为跨进程或跨服务 A2A 消息，但当前阶段避免引入序列化、网络、权限、重试和服务治理复杂度。

## 4. 第一阶段：逻辑多 Agent

### 目标

把现有 LangGraph 节点封装为多个 Agent class，但保持外部行为不变。这一阶段是架构整理，不做新的业务能力。

### 建议新增结构

```text
backend/agromech_api/rag/agent/
  agents/
    __init__.py
    base.py
    query_analyst.py
    router.py
    retrieval.py
    planner.py
    query_rewrite.py
    answer_writer.py
```

### Agent 职责

| Agent | 职责 | 对应现有逻辑 |
| --- | --- | --- |
| `QueryAnalystAgent` | 解析用户问题、型号、品牌、故障码、安全敏感性 | `parse_node` |
| `RouterAgent` | 判断文本、视觉或混合路径 | `route_node` |
| `RetrievalAgent` | 调用现有检索工具，返回 evidence 和 citation | `retrieve_node` |
| `PlanningAgent` | 判断是否需要补检索或 query rewrite | `planner_node` |
| `QueryRewriteAgent` | 基于缺失证据重写查询 | `rewrite_node` |
| `AnswerWriterAgent` | 基于最终证据生成回答 | `answer_node` |

### 数据流

```text
QueryAnalystAgent
  -> RouterAgent
  -> RetrievalAgent
  -> PlanningAgent
  -> Evidence check
  -> QueryRewriteAgent, if needed
  -> AnswerWriterAgent
```

### 验收标准

- `/qa/text` 返回结构不变。
- `/qa/image` 返回结构不变。
- `answer`、`sections`、`citations`、`trace_id`、`uncertainty`、`safety_warnings`、`agent_trace` 兼容现有前端。
- 现有问答测试通过。
- `agent_trace` 能看到明确 Agent 名称或步骤来源。
- 不改前端、不改 worker、不引入 A2A 网络通信。

## 5. 第二阶段：专门审查 Agent

### 目标

增加明确的证据审查和安全审查边界，让回答生成前后都有 guard。

### 新增 Agent

```text
EvidenceReviewerAgent
SafetyReviewerAgent
```

### EvidenceReviewerAgent

负责回答生成前的证据准入：

- 检查 `final_evidence` 是否为空。
- 检查 `citations` 是否可访问。
- 检查证据是否匹配用户问题。
- 检查型号、故障码、部件和保养项是否混淆。
- 判断是否允许生成确定性回答。
- 判断是否需要 query rewrite。

建议输出：

```json
{
  "status": "sufficient",
  "allowed_to_answer": true,
  "missing": [],
  "reason": "evidence covers the requested model and fault code",
  "confidence": 0.84
}
```

### SafetyReviewerAgent

负责回答生成后的安全和合规检查：

- 识别液压、电气、发动机、制动、旋转部件等高风险主题。
- 检查回答是否包含必要安全提醒。
- 检查是否出现无来源维修步骤、油液规格、扭矩、配件号等内容。
- 检查 prompt 注入，如要求忽略引用、绕过安全规则或编造结论。
- 必要时补安全提醒、降低确定性或改为拒答。

### 数据流

```text
RetrievalAgent
  -> EvidenceReviewerAgent
  -> QueryRewriteAgent, if evidence is insufficient and retry remains
  -> AnswerWriterAgent
  -> SafetyReviewerAgent
  -> final response
```

### 验收标准

- 无证据时不会生成确定维修结论。
- 高风险维修问题必须返回 `safety_warnings`。
- 型号不匹配或证据不足时会提示适用范围不确定。
- prompt 注入要求忽略引用时会拒答。
- `agent_trace` 包含 `evidence_review` 和 `safety_review`。

## 6. 第三阶段：按问题类型分流

### 目标

让不同问题类型使用不同领域 Agent 的回答策略，但继续共享检索、证据审查和安全审查。

### 建议新增 Agent

```text
MaintenanceAgent
FaultDiagnosisAgent
PartsAgent
VisualInspectionAgent
```

### 分工

| Agent | 适用问题 | 重点约束 |
| --- | --- | --- |
| `MaintenanceAgent` | 保养周期、油液规格、滤芯更换、例行检查 | 必须说明周期、规格、型号适用性和来源 |
| `FaultDiagnosisAgent` | 故障码、故障现象、可能原因、排查步骤 | 必须区分现象、原因、检查步骤、适用范围和安全提醒 |
| `PartsAgent` | 配件号、部件名称、兼容型号、替代件 | 不能编造配件号，必须说明适配边界 |
| `VisualInspectionAgent` | 图片、图纸、仪表盘、故障灯、部件照片 | 视觉结果只能作为检索线索，不能直接作为维修结论 |

### 数据流

```text
QueryAnalystAgent
  -> RouterAgent determines question_type
  -> RetrievalAgent
  -> EvidenceReviewerAgent
  -> Domain Agent
  -> SafetyReviewerAgent
```

### 验收标准

- 不同类型问题的 `sections` 更稳定。
- 故障类回答不会退化成泛泛说明。
- 保养类回答必须保留周期、规格和适用范围。
- 配件类问题不会编造配件号。
- 图片问题不会把视觉识别结果直接当成确定维修依据。

## 7. 第四阶段：并行检索 Agent

### 目标

把混合检索中的多个通道拆成可并行的检索 Agent，提高召回、排障和降级能力。

### 建议新增 Agent

```text
KeywordRetrievalAgent
StructuredRetrievalAgent
VectorRetrievalAgent
VisualRetrievalAgent
EvidenceMergeAgent
RerankAgent
```

### 数据流

```text
QueryAnalystAgent
  -> parallel:
       KeywordRetrievalAgent
       StructuredRetrievalAgent
       VectorRetrievalAgent
       VisualRetrievalAgent
  -> EvidenceMergeAgent
  -> RerankAgent
  -> EvidenceReviewerAgent
  -> AnswerWriterAgent or Domain Agent
  -> SafetyReviewerAgent
```

### 通道结果格式

```json
{
  "channel": "vector",
  "status": "degraded",
  "reason": "embedding provider timeout",
  "candidates": [],
  "duration_ms": 1200
}
```

### 验收标准

- 任一检索通道失败不会拖垮整体问答。
- trace 能显示每个通道的状态、耗时、候选数量和降级原因。
- 同一 chunk 被多通道命中时能合并 channel。
- rerank 前后排名可追踪。
- 最终 citations 仍保持稳定。

## 8. 推荐实施顺序

```text
1. 定义 BaseAgent、AgentResult 和 trace 规范。
2. 把现有 graph 节点封装成逻辑 Agent，保持行为不变。
3. 引入 EvidenceReviewerAgent，替换零散证据检查边界。
4. 引入 SafetyReviewerAgent，做生成后 guard。
5. 增加问题类型分流和领域 Agent。
6. 拆分并行检索 Agent。
```

第一轮代码修改建议只覆盖第 1 和第 2 步。这样可以把风险控制在结构重组层面，并用现有测试证明行为没有回归。

当前实现落点：

- 进程内 Agent 契约位于 `backend/agromech_api/rag/agent/agents/base.py`。
- LangGraph 仍由 `backend/agromech_api/rag/agent/graph.py` 负责连接节点。
- 第一、二、三阶段的问答侧 Agent 已接入主 graph。
- 第四阶段的检索通道 Agent 位于 `backend/agromech_api/rag/retrieval/hybrid.py`，作为 hybrid retrieval 的底层编排单元。

## 9. 风险和取舍

### 为什么不直接做完整 A2A

完整 A2A 协议需要解决网络通信、消息序列化、版本兼容、鉴权、超时、重试、幂等、审计和服务部署问题。当前 Agent 都在同一个后端进程内，直接引入这些复杂度收益不高。

### 为什么不做自由多 Agent

AgroMech 的核心约束是可信和可追溯。维修、安全、配件和故障判断都必须受证据约束。自由多 Agent 讨论容易让责任边界变模糊，也更难测试。

### 推荐取舍

采用受控多 Agent：

- 流程固定。
- 输入输出结构化。
- 每个 Agent 可单测。
- 每个 Agent 写 trace。
- 最终回答必须受 EvidenceReviewerAgent 和 SafetyReviewerAgent 约束。

## 10. 测试策略

每个阶段都应按测试先行推进：

- Agent 单元测试：验证每个 Agent 的输入输出和 trace。
- Graph 集成测试：验证 LangGraph 节点组合后的流程不变。
- QA 回归测试：验证 `/qa/text` 和 `/qa/image` 返回结构兼容。
- 安全测试：验证高风险问题、证据不足、prompt 注入和型号混淆。
- 检索测试：第四阶段验证通道降级、合并、rerank 和 trace。

第一阶段重点测试：

```text
QueryAnalystAgent.run()
RouterAgent.run()
RetrievalAgent.run()
PlanningAgent.run()
QueryRewriteAgent.run()
AnswerWriterAgent.run()
AgentController.answer_text()
```

## 11. 当前不做的事

- 不修改前端。
- 不修改上传、资料库和 worker 导入链路。
- 不拆分独立 Agent 服务。
- 不实现网络级 A2A 协议。
- 不改变 `/qa/text` 和 `/qa/image` 响应结构。
- 不启用自由 ReAct Agent。
- 不在第一阶段改变检索排序、回答策略或 safety 规则。
