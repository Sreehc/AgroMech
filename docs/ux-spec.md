# UX 规格

本文描述当前前端真实功能和交互边界。

## 1. 应用结构

当前前端页面：

- `/login`：登录页。
- `/`：Assistant 问答工作台。
- `/library`：资料库。

全局布局由 `AppFrame` 提供会话检查、导航和登录跳转。未登录访问业务页会跳转 `/login`，登录成功后回到原目标页。

## 2. 角色体验

- `admin`、`maintainer`：可上传、重处理、删除资料。
- `user`、`evaluator`：可问答、查看资料和证据；不显示资料管理操作。
- `evaluator` 可通过后端获得完整 retrieval trace；普通用户只看脱敏摘要。

## 3. Assistant 工作台

主要组件：

- `AssistantWorkbench`
- `Assistant`
- `StructuredAnswerCard`
- `EvidencePanel`
- `FilterControls`
- `VisualAnnotation`

能力：

- 输入文本问题。
- 附加单张图片并走 `/qa/image`。
- 设置上下文筛选：brand、model、document_type、language。
- 通过 assistant-ui runtime 调用前端 `/api/chat`，由该路由转发后端 `/qa/text` 或 `/qa/image`。
- 展示 answer、sections、citations、uncertainty、safety_warnings。
- 点击引用打开证据面板。
- 通过 `trace_id` 拉取 retrieval trace 摘要。
- 会话携带 `session_id` 时，问答写入后端会话历史。

图片回答额外展示：

- 原图缩略图。
- OCR 文本。
- 检测实体。
- 视觉置信度。
- 可用时显示 normalized bbox 标注。

## 4. 资料库页面

主要组件：

- `LibraryPage`
- `DocumentUploadQueue`
- `LibraryDocumentList`
- `DocumentDetailView`
- `DocumentPreviewPanel`

能力：

- 按 brand、model、document_type、language、status 筛选资料。
- 展示总数、处理中、已索引、失败等状态概览。
- 展开单个资料查看摘要、最近任务和失败信息。
- 管理角色可打开上传队列，批量选择文件并逐项上传。
- 上传前校验扩展名和大小。
- 上传中可显示进度、失败、重试、移除。
- 有活跃上传时关闭队列需要确认。
- 管理角色可重处理和删除资料，操作前弹确认框。

资料状态显示：

- `queued`：已排队。
- `processing`：处理中。
- `indexed`：已索引。
- `failed`：处理失败。
- `reprocessing`：重新处理中。
- `deleting`：删除中。
- `deleted`：已删除。

## 5. 文档详情和预览

文档详情展示：

- 元数据。
- 当前状态。
- failure stage/code/message。
- recent task。
- chunk 摘要。

证据预览：

- 文本证据显示 source locator 和高亮文本。
- PDF/page 证据显示 page image URL 和区域高亮。
- 删除或不可访问资料不空白，显示不可访问状态。

## 6. 会话历史

当前会话能力：

- 后端会话 CRUD。
- 前端 local fallback。
- 会话保存 messages、filters、has_image。
- Assistant 当前 filters 会随 active session 持久化。

## 7. 错误和空状态

- API 请求失败以页面 alert 或局部 error state 展示。
- trace 加载失败不影响引用展示。
- 资料筛选无结果时展示清空筛选入口。
- 未知 document status 使用中性“未知状态”。
- 低置信图片且无文字问题时提示用户补充图片、型号、故障码或文字描述。

## 8. 当前边界

- 前端只支持单图问答。
- 当前没有完整评估管理 UI。
- 当前没有移动端专门页面。
- 当前没有前端展示完整 `agent_trace` 的专用调试视图；后端响应和测试已覆盖该字段。
