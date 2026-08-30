# qq-mediary-stack

在 QQ 里发一个片名，剩下的全自动：搜索资源 → 转存进夸克网盘 → 生成 STRM → 极影视刮削 → 在线播放。剧集自动追更，缺集自动补。

为**极空间 NAS + 夸克网盘 + 极影视**场景设计的一体化编排，不需要 PT 站、不需要 115 会员、资源不下载到本地（在线播放）。

## 架构

```
QQ 私聊片名
   │
   ▼
qqbot（本仓库）── 调用 agent API ──▶ Mediary Scout（上游项目，LLM agent）
                                       │ 搜索（内置 PanSou + TMDB）
                                       ▼
                                   转存进夸克网盘
                                       │
strm-sync（本仓库）：定时/触发式扫描 ◀──┘
                                       │
                                       ▼
                          OpenList Strm 驱动生成 .strm 文件
                                       │
                                       ▼
                          极影视刮削本地 strm → 点播时经 OpenList
                          本地代理在线播放（不下载、不走官方挂载限速）
```

## 组成部分

| 组件 | 来源 | 职责 |
|---|---|---|
| Mediary Scout | [上游 fancydirty/mediary-scout](https://github.com/fancydirty/mediary-scout)（0BSD，脚本自动拉取构建） | LLM agent 搜索、转存、验证、缺集追踪 |
| qqbot | 本仓库 | QQ 机器人壳：收发消息、白名单、候选选择、完成通知、/刷新 |
| strm-sync | 本仓库 | 定时 + 触发式遍历 OpenList Strm 驱动目录，生成本地 strm 文件 |
| OpenList | 上游镜像 | 挂载夸克（本地代理）、Strm 驱动 |
| postgres / pansou | 上游镜像 | 数据库 / 资源搜索引擎 |

## 前置条件

- 夸克网盘账号（建议 SVIP，88VIP 送的即可）
- OpenAI 兼容 LLM key（DeepSeek 等，agent 搜片用，按量计费很便宜）
- QQ 开放平台的机器人 AppID/AppSecret（申请流程见 [docs/QQ机器人申请与配置指南.md](docs/QQ机器人申请与配置指南.md)，个人主体可注册，约 15 分钟）
- 极空间 NAS（或其他任何能跑 docker compose 的机器）

## 快速开始

```bash
git clone https://github.com/keru-s/qq-mediary-stack.git
cd qq-mediary-stack
cp .env.example .env   # 填入 LLM key、QQ 凭证等
./scripts/build-images.sh
docker compose up -d
```

极空间面板部署（无需 SSH）和极影视建库的完整图文教程：[docs/极空间部署与极影视建库教程.md](docs/极空间部署与极影视建库教程.md)

## 使用

- QQ 私聊机器人发片名/剧名 → 自动获取，多候选时回序号选择
- `/状态` 查看进行中任务；`/刷新` 立即更新媒体库；`/帮助` 查看说明

## 常见问题

- **官方网盘挂载播放卡**：极空间官方挂载会被夸克限速，本项目走 OpenList 本地代理，实测流畅
- **STRM 播放 401**：Strm 驱动必须开「携带签名（withSign）」
- **机器人不回消息**：检查 QQ 开放平台沙箱配置；QQ openid 按应用隔离，首次配置看容器日志拿真实 openid
- **改 .env 后无效**：必须重建容器（面板里「重新构建」），重启不生效

## 致谢与许可

- [Mediary Scout](https://github.com/fancydirty/mediary-scout)（0BSD）：核心的 agent 获取引擎
- [OpenList](https://github.com/OpenListTeam/OpenList)（AGPL-3.0）：网盘挂载与 STRM
- [PanSou](https://github.com/fish2018/pansou-web)：网盘资源搜索
- [botpy](https://github.com/tencent-connect/botpy)：QQ 官方机器人 SDK

本仓库自身的代码（qqbot、strm-sync、编排与文档）以 [MIT](LICENSE) 发布。
