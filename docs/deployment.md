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

`/opt/agromech/.env` 可参考 `deploy/env.prod.example`，必须填真实的 `DATABASE_URL`、`RABBITMQ_URL`、`AUTH_TOKEN_SECRET`、百炼配置和存储配置。Postgres 容器必须安装 pgvector；Alembic 迁移会在目标库执行 `CREATE EXTENSION IF NOT EXISTS vector`。

## 2. Nginx

前端静态文件部署到 `/var/www/agromech`。正式访问域名当前为 `agromech.wandcheers.xyz`；需要先在 DNS 中把它解析到服务器公网 IP。

宿主 Nginx 使用 HTTPS 对外服务，HTTP 自动跳转到 HTTPS。证书由 Certbot/Let's Encrypt 管理：

```bash
certbot --nginx -d agromech.wandcheers.xyz --redirect
```

服务器已有 `certbot-renew.timer` 时会自动续期。宿主 Nginx 需要把 `/backend/` 反代到后端容器：

```nginx
location /backend/ {
    proxy_pass http://127.0.0.1:8000/;
}
```

完整示例见 `deploy/nginx.agromech.conf`。如果上传大文件，保留 `client_max_body_size 120m` 或更高。

## 3. 首次部署

服务器准备：

```bash
mkdir -p /opt/agromech/data /var/www/agromech
cp deploy/env.prod.example /opt/agromech/.env
cp deploy/docker-compose.prod.yml /opt/agromech/docker-compose.yml
```

编辑 `/opt/agromech/.env` 后启动：

```bash
cd /opt/agromech
docker compose pull
docker compose run --rm api python -m alembic upgrade head
docker compose up -d api worker
```

pgvector 迁移完成后，重建既有文档向量：

```bash
docker compose run --rm api python scripts/rebuild-vector-index.py
```

创建首个管理员：

```bash
docker compose run --rm api python scripts/create-user.py --username admin --role admin --display-name "Administrator"
```

## 4. GitHub Actions 自动部署

Workflow：`.github/workflows/deploy.yml`。

需要配置 GitHub Secrets：

- `DEPLOY_HOST`：服务器地址。
- `DEPLOY_USER`：SSH 用户。
- `DEPLOY_SSH_KEY`：SSH 私钥。
- `DEPLOY_APP_PATH`：例如 `/opt/agromech`。
- `DEPLOY_FRONTEND_PATH`：例如 `/var/www/agromech`。
- `GHCR_USERNAME`：可选。默认使用触发 workflow 的 GitHub 用户。
- `GHCR_TOKEN`：可选。默认使用本次 workflow 的 `github.token`。

部署用户需要具备：

- 写入 `DEPLOY_APP_PATH` 和 `DEPLOY_FRONTEND_PATH` 的权限。
- 运行 `docker compose` 的权限。
- 执行 `sudo nginx -t` 和 `sudo systemctl reload nginx` 的权限。

workflow 会在部署期间让服务器登录 GHCR。默认使用本次 Actions job 的 `github.token`；只有需要改用固定 PAT 或专用机器账号时，才需要配置 `GHCR_USERNAME`/`GHCR_TOKEN`。

推送 `main` 或手动触发 workflow 后会执行：

1. Python/前端测试和构建。
2. 构建后端镜像并推送到 GHCR。
3. 同步 `frontend/out/` 到服务器静态目录。
4. 上传 compose 文件。
5. 在服务器执行 `docker login ghcr.io`。
6. 执行 Alembic 迁移。
7. 重启 `api` 和 `worker`。
8. reload 宿主 Nginx。

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
docker logs agromech-api --tail=100
docker logs agromech-worker --tail=100
```

如果前端登录失败，先检查浏览器请求的 `/backend/auth/login` 是否被 Nginx 正确反代。
