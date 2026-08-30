# AGENTS.md

给在本仓库工作的 agent 的上下文。人类用户教程在 README.md 和 docs/。

## 这个仓库是什么
影视自动化胶水层：QQ 机器人（qqbot/）→ Mediary Scout agent API → 夸克网盘 → OpenList Strm → 极影视。
本仓库只拥有 qqbot/、strm-sync/、docker-compose.yaml、scripts/、docs/。Mediary Scout 是上游依赖（0BSD），构建时由 scripts/build-images.sh 克隆到 vendor/（已 gitignore），不要把上游源码复制进本仓库。

## 构建与验证
- 构建全部镜像：`./scripts/build-images.sh`（x86 NAS 目标加 `--platform linux/amd64`）
- 改动校验：`python3 -m py_compile qqbot/bot.py strm-sync/sync.py && docker compose config -q`
- 完成标准：两条命令都通过；涉及 QQ 或 OpenList 行为的改动，在自述里写明「未经真机联调」的部分

## 关键约定（改代码前必读）
- **docker-compose.yaml 必须兼容老版 compose 解析器**（极空间面板）：禁止 `env_file` 映射写法、禁止 profiles、镜像名写死不拼接变量。用户的 .env 通过面板的「添加 .env」整段粘贴注入。
- 所有用户配置走 `.env`，单一事实来源是 `.env.example`；新增配置项时同步改它和 compose。
- 凭据（QQ Secret、LLM key、token）永远不提交，.env 已 gitignore。

## 已知陷阱（都是真踩过的）
- **botpy 在 import 时注册 root handler 并把级别压到 WARNING**：qqbot 的 logging.basicConfig 必须带 `force=True`，否则自定义日志被静默吞掉。
- **QQ user_openid 按应用隔离**：不同机器人下同一 QQ 的 openid 不同，不能跨应用复用。
- **OpenList Strm 驱动不带签名会 401**：`withSign` 必须开；签名永久有效。
- **OpenList 目录有缓存**：strm-sync 的 fs/list 必须 `refresh: true`，否则夸克侧的变更不可见。
- **Mediary Scout agent API**：`POST /api/agent/acquire`（body `{query}` 或 `{tmdbId}`，409=歧义带 candidates）和 `GET /api/agent/activity`，Bearer token 来自 `MEDIA_TRACK_AGENT_TOKEN`；未配置 token 时端点返回 404（不是 401）。
- **改 .env 后必须重建容器**，重启不生效（env 在容器创建时烧入）。

## 服务拓扑
compose 内网：qqbot → web:3000；qqbot → strm-sync:8000（POST /scan 即时触发）；strm-sync → openlist:5244；web → postgres、pansou。宿主只暴露 3300（web）和 5244（openlist）。
