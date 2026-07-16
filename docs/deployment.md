# 部署说明

本文记录当前推荐的服务器部署方式：前端静态文件由宿主 Nginx 托管，后端 API 和 worker 使用 Docker，Postgres 与 RabbitMQ 使用服务器上已有服务。

## 1. 服务器目录

建议目录：

```text
/opt/agromech/
  docker-compose.yml
  .env
  data/

/var/www/agromech/
  index.html
  _next/
```

`/opt/agromech/.env` 可参考 `deploy/env.prod.example`，必须填真实的 `DATABASE_URL`、`RABBITMQ_URL`、`AUTH_TOKEN_SECRET`、百炼配置和存储配置。生产库已有文档或评测题时还必须配置 `RETRIEVAL_BASELINE_PATH`；只有首次空库部署可以暂时留空。Postgres 容器必须同时安装 pgvector 和 ParadeDB `pg_search`；Alembic 会创建 `vector`、`pg_search` 扩展及 `ix_chunk_search_index_bm25`。使用 Docker PostgreSQL 时，镜像必须提供这两项扩展。

## 2. Nginx

前端静态文件部署到 `/var/www/agromech`。正式访问域名当前为 `agromech.wandcheers.xyz`；需要先在 DNS 中把它解析到服务器公网 IP。

宿主 Nginx 使用 HTTPS 对外服务，HTTP 自动跳转到 HTTPS。证书由 Certbot/Let's Encrypt 管理：

```bash
certbot --nginx -d agromech.wandcheers.xyz --redirect
```

服务器已有 `certbot-renew.timer` 时会自动续期。宿主 Nginx 通过独立 upstream 文件把 `/backend/` 反代到当前后端槽位：

```nginx
location /backend/ {
    include /etc/nginx/conf.d/agromech-backend-upstream.conf;
}
```

完整站点示例见 `deploy/nginx.agromech.conf`。首次部署时先由运维安装站点配置和初始 `blue` 槽位 upstream（端口 `8000`）：

```bash
sudo install -m 644 deploy/nginx.agromech.conf /etc/nginx/sites-available/agromech
printf 'proxy_pass http://127.0.0.1:8000/;\n' | \
  sudo tee /etc/nginx/conf.d/agromech-backend-upstream.conf >/dev/null
sudo nginx -t && sudo systemctl reload nginx
```

将站点路径保存为 GitHub Secret `DEPLOY_NGINX_SITE_PATH`，例如 `/etc/nginx/sites-available/agromech`。workflow 在候选 API 验证通过后才会替换此文件和 upstream 文件；它不能替代首次安装 Nginx 站点配置。如果上传大文件，保留 `client_max_body_size 120m` 或更高。

## 3. 首次部署

服务器准备：

```bash
mkdir -p /opt/agromech/data /var/www/agromech
cp deploy/env.prod.example /opt/agromech/.env
cp deploy/docker-compose.prod.yml /opt/agromech/docker-compose.yml
```

编辑 `/opt/agromech/.env` 后，先记录当前检索评估基线并完成数据库备份。

### 发布评测基线

生产库已有文档或评测题时，`RETRIEVAL_BASELINE_PATH` 是必填配置，必须指向容器内可读、版本化的基线 JSON。`docker-compose.prod.yml` 会将宿主 `/opt/agromech/data` 挂载为容器 `/app/.agromech-data`，因此推荐在 `.env` 中使用如下路径，并在宿主保留同名文件：

```dotenv
RETRIEVAL_BASELINE_PATH=/app/.agromech-data/release-evidence/curated-mvp-2026-07.json
```

基线必须来自上一版已验收的真实 `curated-mvp` 运行结果，包含 `recall_at_20`、`ndcg_at_10` 和 `retrieval_p95_ms`。不要将生产数据或环境测量结果提交到 Git、写入镜像，或用合成开发数据替代。通过受控的发布证据存储或加密制品通道分发到服务器后，限制宿主文件权限：

```bash
install -d -m 700 /opt/agromech/data/release-evidence
install -m 600 /secure-release-evidence/curated-mvp-2026-07.json \
  /opt/agromech/data/release-evidence/curated-mvp-2026-07.json
```

发布 workflow 会先检查数据库是否存在未删除文档或评测题。有数据时会验证 `RETRIEVAL_BASELINE_PATH` 已配置且文件可读，然后执行：

```bash
python scripts/evaluate-retrieval.py --baseline "$RETRIEVAL_BASELINE_PATH"
```

路径或文件缺失、评测结果低于基线、两项质量指标均未提升、或 P95 超过基线 1.5 倍时，workflow 会在切换 API、同步前端和 reload Nginx 前失败。只有既无文档也无评测题的首次空库部署可以暂时不配置基线；该分支只跳过历史指标比较和无数据可执行的 QA/Citation smoke，迁移、BM25 索引重建、API readiness 与 Worker 依赖预检仍会执行。尚未取得真实 `curated-mvp` 基线时，不得在已有数据的生产库触发切换，也不得用合成结果伪造基线。

### 升级前数据库检查

迁移前只检查 PostgreSQL 是否提供所需扩展。首次升级时 `ix_chunk_search_index_bm25` 尚不存在，因此不能在此阶段用索引检查阻断发布：

```sql
SELECT extname
FROM pg_extension
WHERE extname IN ('vector', 'pg_search')
ORDER BY extname;
```

### 启动与迁移

```bash
cd /opt/agromech
docker compose --project-name agromech pull
docker compose --project-name agromech run --rm api python -m alembic upgrade head
```

### 迁移完成后重建并验证

迁移完成后，重建既有文档的 BM25 搜索行和 Dense 向量：

```bash
docker compose --project-name agromech run --rm api python scripts/rebuild-vector-index.py
```

重建命令会使用当前 `.env` 中配置的文本 embedding provider 重建 `chunk_search_index` 与 `chunk_vector_embeddings`，并在 PostgreSQL 上确认 BM25 索引存在；不会迁移旧向量文件。也可额外执行：

```sql
SELECT indexname
FROM pg_indexes
WHERE indexname = 'ix_chunk_search_index_bm25';
```

只有完成以上验证后，首次部署才启动初始 `blue` 服务：

```bash
cd /opt/agromech
docker compose --project-name agromech up -d api worker
```

已有服务升级必须使用下文的自动蓝绿发布，不能直接运行 `docker compose up -d api worker` 覆盖正在被 Nginx 代理的槽位。发布顺序必须为：

```text
record baseline -> backup -> install extensions -> alembic upgrade
-> rebuild indexes -> evaluation gate -> start inactive candidate slot
-> /health/ready and QA/Citation smoke -> worker dependency preflight
-> atomically switch Nginx upstream -> verify old Worker stopped -> replace worker -> stop previous API -> monitor
```

候选槽位在切流前只执行 Worker 依赖预检：连接数据库、连接 RabbitMQ 并声明目标队列，但不注册 RabbitMQ consumer，因此不会与旧 Worker 竞争真实任务。任何数据库迁移、重建、基线评测、`/health/ready`、QA/Citation 冒烟或该预检失败，都不得切换应用版本。`/health/ready` 返回 `503` 时，保留当前服务并排查 `vector`、`pg_search` 或 BM25 索引。Nginx 切换成功后，workflow 会先停止并确认旧 Worker 已退出，再启动候选 Worker。停止旧 Worker 失败或启动候选 Worker 失败时，workflow 必须恢复旧 upstream 并重启、确认旧 Worker 运行后才清理候选槽位；Nginx 文件恢复、`nginx -t`、reload 或旧 Worker 恢复的任一步骤失败，部署显式失败并保留候选槽位供诊断，绝不宣称已恢复。

创建首个管理员：

```bash
docker compose --project-name agromech run --rm api python scripts/create-user.py --username admin --role admin --display-name "Administrator"
```

## 4. GitHub Actions 自动部署

Workflow：`.github/workflows/deploy.yml`。

需要配置 GitHub Secrets：

- `DEPLOY_HOST`：服务器地址。
- `DEPLOY_USER`：SSH 用户。
- `DEPLOY_SSH_KEY`：SSH 私钥。
- `DEPLOY_APP_PATH`：例如 `/opt/agromech`。
- `DEPLOY_FRONTEND_PATH`：例如 `/var/www/agromech`。
- `DEPLOY_NGINX_SITE_PATH`：已安装的 Agromech Nginx 站点配置，例如 `/etc/nginx/sites-available/agromech`。
- `GHCR_USERNAME`：可选。默认使用触发 workflow 的 GitHub 用户。
- `GHCR_TOKEN`：可选。默认使用本次 workflow 的 `github.token`。

部署用户需要具备：

- 写入 `DEPLOY_APP_PATH` 和 `DEPLOY_FRONTEND_PATH` 的权限。
- 运行 `docker compose` 的权限。
- 执行 `sudo nginx -t` 和 `sudo systemctl reload nginx` 的权限。

workflow 会在部署期间让服务器登录 GHCR。默认使用本次 Actions job 的 `github.token`；只有需要改用固定 PAT 或专用机器账号时，才需要配置 `GHCR_USERNAME`/`GHCR_TOKEN`。

当前仓库不跟踪测试源码；`scripts/test-all.sh` 只执行前端 lint 和生产构建。生产发布的运行时门禁由候选环境中的迁移、索引重建、readiness、QA/Citation smoke 和 Worker 依赖预检承担。

推送 `main` 或手动触发 workflow 后会执行：

1. 执行前端 lint 和生产构建。
2. 构建后端镜像并推送到 GHCR。
3. 上传 compose 文件。
4. 在服务器执行 `docker login ghcr.io`。
5. 读取 Nginx 当前 upstream（`blue:8000` 或 `green:8001`），在另一个 Compose project 和端口启动候选镜像。先在候选容器中执行 Alembic 和索引重建；已有文档或评测题时，要求 `RETRIEVAL_BASELINE_PATH` 指向可读的版本化真实生产基线，并运行 `scripts/evaluate-retrieval.py --baseline "$RETRIEVAL_BASELINE_PATH"`。首次空库部署只跳过该历史指标比较。
6. 仅访问候选端口执行 `/health/ready`。QA smoke 优先使用当前数据集的首个评测题，没有评测题时使用首个公开、已索引文档的受限长度片段构造查询；响应必须带 Citation，trace 必须记录 Dense、BM25、RRF 与 Citation 成功状态。严格空库没有可用查询时只跳过 QA/Citation smoke。任何已执行门禁的失败都会停止在候选槽位，Nginx 仍代理旧版本。
7. 候选 API 的验证通过后，workflow 在候选 Worker 容器中完成数据库/RabbitMQ 连通性与队列声明预检，不注册 consumer。随后备份现有 Nginx 站点/upstream 文件，再以 `nginx -t` 和 reload 原子切换 upstream；切换成功后才停止并确认旧 Worker 已退出，再启动候选 Worker。旧 Worker 停止失败或候选 Worker 启动失败时，workflow 会恢复 upstream 并重启、确认旧 Worker；只有所有恢复步骤成功才清理候选槽位。站点/upstream 恢复、配置测试、reload 或旧 Worker 重启/运行检查失败时，workflow 显式失败并保留候选槽位，避免 Nginx 指向已清理的候选 API；最后停止旧 API、同步 `frontend/out/`，随后持续监控。

## 5. 静态前端约束

前端使用 `next.config.ts` 的 `output: "export"`，不能依赖 Next server route。当前约束：

- 浏览器直接请求 `/backend/*`。
- `/backend/*` 必须由宿主 Nginx 反代到 FastAPI。
- 聊天不再使用 `/api/chat`，而是直接调用 `/backend/qa/text` 或 `/backend/qa/image`。
- 文档详情使用静态路径 `/library/document?id=<document_id>`。

## 6. 运行检查

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/dependencies
curl -i http://127.0.0.1:8000/health/ready
active_project="$(cat /opt/agromech/.active-backend-project 2>/dev/null || printf '%s' agromech)"
docker compose --project-name "$active_project" logs api --tail=100
docker compose --project-name "$active_project" logs worker --tail=100
```

`/health/ready` 必须返回 `200`；其中 `pgvector` 与 `pg_search` 应为 `ok`，并确认 `pg_search` 的 target 对应当前 schema 的 `ix_chunk_search_index_bm25`。如果前端登录失败，先检查浏览器请求的 `/backend/auth/login` 是否被 Nginx 正确反代。
