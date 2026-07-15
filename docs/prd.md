# 产品需求文档

## 1. 产品定位

AgroMech 是一个面向农机资料的多模态检索增强生成系统。它把说明书、维修手册、故障码表、保养规程、配件目录、图纸、扫描件和现场图片整理成可检索、可引用、可追溯的知识库。

系统不是通用聊天机器人，而是农机资料证据助手：维修结论必须来自可追溯证据；证据不足时必须说明不确定性。

## 2. 目标用户

- 农机维修人员。
- 经销商和售后服务人员。
- 合作社、农场、农机服务组织技术负责人。
- 农机方向学生、教师和研究人员。
- 需要构建私有农机知识库的团队。

## 3. 当前已实现能力

### 文档管理

- 支持上传 PDF、DOCX、XLSX、CSV、TXT、Markdown、PNG、JPG、JPEG、WEBP。
- 上传时校验类型、大小和重复文件。
- 支持资料列表、筛选、详情、预览、重新处理和删除。
- 删除采用软删除和后台清理链路，历史 citation 保留不可访问提示。

### 导入和索引

- Worker 使用 `ingest_tasks` 状态机处理 `ingest`、`reprocess`、`delete`。
- 支持文本、表格、PDF 页面渲染、图片 OCR、视觉观察、LLM 元数据回填、实体抽取、`pg_search` BM25 索引和 pgvector 向量索引；Graph RAG 明确不在主链路启用。
- 支持 RabbitMQ 分发上传、重处理和删除任务；DB task 仍是权威状态。
- 处理失败会记录阶段、错误码和错误信息；超过重试上限进入 `dead`。

### 问答

- `POST /qa/text` 支持文本问答、filters、session_id 和安全拒答。
- `POST /qa/image` 支持单图问答，提取 OCR、视觉描述和可见实体后进入检索。
- 问答经过 Agent Controller 和 LangGraph 工作流，返回 `answer`、`sections`、`citations`、`trace_id`、`uncertainty`、`safety_warnings`、`agent_trace`。
- 当前 Agentic RAG 为工程可控版本：规则优先路由、检索前 Query Rewrite、最多 2 轮补检索、生成前证据准入与 Citation 契约。

### 检索和证据

- 主检索链路为 Dense + BM25，并以 RRF 融合后进入 Rerank；filters 仅限制检索范围，不是独立召回或加权通道。
- 结果按 chunk 去重，保留 Dense、BM25、RRF、Rerank 和降级 trace。
- 最终回答的 Citation 只能来自通过证据准入的最终 evidence；证据不足时返回高不确定性回答且不伪造 Citation。

### 前端

- 登录页。
- Assistant 问答工作台。
- 资料库页面和上传队列。
- 文档详情、证据预览和 trace 摘要。
- 会话历史和筛选上下文。

## 4. 权限范围

- `admin`：全部功能。
- `maintainer`：资料维护和问答。
- `user`：问答、资料阅读、自己的会话。
- `evaluator`：问答、资料阅读、完整 trace 调试。

资料上传、删除、重新处理仅 `admin` 和 `maintainer` 可操作。

## 5. 安全和可信要求

- 回答不得编造维修步骤、故障原因、保养周期、油液规格、扭矩、配件号。
- 涉及液压、电气、发动机、制动或旋转部件时必须保留安全提醒。
- Prompt 注入要求忽略引用、绕过安全规则或编造资料时，系统应拒答。
- 视觉识别结果只能作为检索线索，不能在没有文档证据时直接变成确定维修结论。
- API key、token、password、内部路径、异常栈不得暴露给普通用户。

## 6. 当前边界

- 默认只支持单图问答，多图上传会拒绝。
- RabbitMQ 默认发布关闭，本地可继续用 DB 队列一次性 worker；生产或联调时设置 `RABBITMQ_PUBLISH_ENABLED=true`。
- 路由层和证据检查层保留 LLM 接入点，但当前生产路径以规则判断为主；导入完成后的文档元数据回填使用 LLM。
- 生成前校验当前是 evidence guard，不是生成后逐 claim 归因校验。
- 精确 bounding box、图片相似检索、完整移动端、桌面端打包和 LangGraph checkpoint 持久化仍是后续增强。

## 7. 验收口径

当前版本可验收条件：

- 上传文档能创建 task，并由 worker 处理到 `indexed` 或可解释失败状态。
- 文本/图片问答能返回来源引用、trace 和 agent_trace。
- Dense + BM25、RRF、Rerank、Query Rewrite 和 Citation 的 trace 可追溯，受保护型号、故障码和零件号不会被重写破坏。
- 明确型号问题不明显混淆不同型号。
- 删除资料后新检索不可见，历史引用仍可展示不可访问状态。
- 后端、worker 和前端测试通过。
