# AgroMech 前端

AgroMech RAG 的 Web 前端，基于 Next.js App Router + React + TypeScript + Tailwind CSS + assistant-ui。它提供登录、农机资料问答工作台和资料库管理界面，通过 `/backend/*` 调用 FastAPI 后端。

## 技术栈

- Next.js App Router（静态导出，`next.config.ts` 中 `output: "export"`）。
- React + TypeScript。
- Tailwind CSS + shadcn/radix 组件。
- assistant-ui 问答运行时。
- Vitest 单元测试，Playwright E2E。

## 页面

- `/login`：登录页。
- `/`：Assistant 问答工作台，支持文本问答和单图问答。
- `/library`：资料库，支持筛选、上传队列、详情、预览。

全局布局由 `AppFrame` 提供会话检查、导航和登录跳转。未登录访问业务页会跳转 `/login`。

## 与后端的关系

前端使用静态导出，不依赖 Next server route。所有业务请求走 `/backend/*`，由宿主 Nginx 反代到 FastAPI：

- 无图片附件时，浏览器通过 `/backend/qa/text` 调用 `POST /qa/text`。
- 带单张图片附件时，浏览器通过 `/backend/qa/image` 调用 `POST /qa/image`。
- 登录、资料库、会话等请求同样走 `/backend/*`。

聊天不使用 Next `/api/chat`，assistant-ui runtime 直接调用后端问答接口。回复展示回答正文、视觉观察、引用来源、安全提醒和 trace 信息。

## 本地开发

```bash
npm install --prefix frontend
npm run dev --prefix frontend
```

指定端口：

```bash
npm run dev --prefix frontend -- -p 3000
```

本地开发需要后端在可反代的 `/backend/*` 路径提供服务；部署方式见根目录 [docs/deployment.md](../docs/deployment.md)。

## 测试和构建

```bash
npm run lint --prefix frontend
npm run test --prefix frontend
npm run build --prefix frontend
npm run e2e --prefix frontend
```

`npm run build` 会产出静态文件到 `frontend/out/`，部署时同步到宿主静态目录。

## 更多文档

- 前端页面和交互规格：[docs/ux-spec.md](../docs/ux-spec.md)。
- 后端 API 规格：[docs/api-spec.md](../docs/api-spec.md)。
- 部署说明：[docs/deployment.md](../docs/deployment.md)。
