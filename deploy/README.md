# 在线受限体验部署

代码目录 `/opt/shuce/current/backend`，独立虚拟环境 `/opt/shuce/venv`，持久数据 `/var/lib/shuce`。服务用户为 `shuce`；后端只监听 `127.0.0.1:8020`，通过 nginx `/shuce-api/` 转发。前端静态文件在 `/var/www/shuce`，使用 `/shuce/` 前缀。

## 构建与配置

```bash
cd frontend
npm ci
VITE_API_BASE=/shuce-api VITE_PUBLIC_DEPLOYMENT=true npm run build -- --base=/shuce/
```

服务器 `/etc/shuce/app.env` 由管理员单独配置，权限 `0600`，不提交到代码仓库：

```dotenv
SHUCE_PUBLIC_DEPLOYMENT=true
SHUCE_ROOT_PATH=/shuce-api
# 在服务器生成独立的随机长口令；此处留空时账号关闭。
SHUCE_PASSWORD_ANALYST=
SHUCE_PASSWORD_ADMIN=
SESSION_ARCHIVE_ENABLED=true
SESSION_ARCHIVE_PATH=/var/lib/shuce/session_archive.db
# Chat / Embedding / Rerank 的地址、模型和独立凭据按 backend/.env.example 配置。
```

启用公开部署模式后，默认演示密码全部关闭。只有配置至少 16 字符独立口令的账号可登录；`analyst` 只拥有 Olist 查询范围，不能导入 CSV 或重建全库索引。口令不会写入网页或 README，线上前端也不显示本地演示账号面板。

打包时须保留 `frontend/src/categoryLabels.json`：后端与前端共用这份品类标签。只复制前端 dist 会导致后端启动失败。`backend/app/skills/` 内的配置和 SKILL.md 也应随应用代码一起部署。

`backend/data` 应链接至持久目录，安装两类数据并以服务用户读写。不要把开发机的历史、记忆、导入数据或登录令牌一同迁移。Schema 索引可在管理员核对数据和模型签名后预置，避免首次打开批量重建；其他部署应按需显式构建。

## 代理和资源

在既有 nginx 的 server 块中 include `nginx-shuce.conf`，独立限流区安装于 http 上下文。提交与字段检索限制为每 IP 每分钟 6 次、短突发 2 次；登录每分钟 5 次、短突发 4 次。它是请求频率限制，不是供应商硬费用预算。任务轮询路径不受这个频率限制。

公开模式最多允许一个运行中异步任务；同步查询入口及全量索引重建在公开代理关闭。systemd 限制内存 650MB、CPU 100%、任务线程 64，只允许持久目录写入。DuckDB 使用 256MB 内存限制、单线程、每个 CSV 4MB 读取缓冲，并关闭插入顺序保留；已用包含范围保护 CTE 的五表经营查询验证。此配置用于当前单机受限体验，不是多租户生产架构。

发布应先比对服务器文件哈希、备份原配置和静态页，执行 `nginx -t` 后再 reload，核验微知、赛博明翰和原作品集入口。不要覆盖其他任务新增的共享配置。当前 IP 入口沿用既有 HTTP 服务；面向长期外部账号使用还应配置域名与 HTTPS。
