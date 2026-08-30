"""Mediary Scout QQ 机器人壳。

链路：QQ C2C 私聊 -> 片名 -> Mediary Scout agent API -> 夸克网盘。

配置全部走环境变量：
  QQ_BOT_APPID / QQ_BOT_APPSECRET   腾讯 QQ 机器人开放平台凭证
  ALLOWED_OPENIDS                   逗号分隔的 user_openid 白名单，不在名单的消息直接忽略
  MEDIARY_API_URL                   Mediary Scout web 地址，compose 内为 http://web:3000
  MEDIARY_AGENT_TOKEN               agent API 的 Bearer token（MEDIA_TRACK_AGENT_TOKEN）

⚠️ 未真实联调过 QQ 开放平台：事件/接口按 qq-botpy 官方文档编写，
   首次启用请先以沙箱频道/沙箱私聊验证。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any

import aiohttp
import botpy
from botpy.message import C2CMessage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    # botpy 在 import 时就会注册 root handler 并把级别压到 WARNING，
    # 不加 force=True 我们的 INFO 日志会被静默吞掉。
    force=True,
)
log = logging.getLogger("mediary-qqbot")

# ---------------------------------------------------------------- 配置

APPID = os.environ.get("QQ_BOT_APPID", "").strip()
APPSECRET = os.environ.get("QQ_BOT_APPSECRET", "").strip()
MEDIARY_API_URL = os.environ.get("MEDIARY_API_URL", "http://web:3000").rstrip("/")
MEDIARY_AGENT_TOKEN = os.environ.get("MEDIARY_AGENT_TOKEN", "").strip()
ALLOWED_OPENIDS = {
    x.strip() for x in os.environ.get("ALLOWED_OPENIDS", "").split(",") if x.strip()
}
# 获取完成后触发 strm-sync 立即扫描，让新内容尽快进极影视（失败不影响主流程）
STRM_TRIGGER_URL = os.environ.get("STRM_TRIGGER_URL", "http://strm-sync:8000/scan").strip()
# 机器人未在开放平台正式上线前，测试消息走沙箱网关，必须 is_sandbox=True 才能收到。
QQ_BOT_SANDBOX = os.environ.get("QQ_BOT_SANDBOX", "1").strip().lower() in ("1", "true", "yes")

POLL_INTERVAL_S = 30
POLL_TIMEOUT_S = 30 * 60
# 等待候选选择的最长时间（秒），超时作废
PENDING_TTL_S = 10 * 60

HELP_TEXT = (
    "直接发片名/剧名即可开始获取。\n"
    "出现多个候选时回复序号选择。\n"
    "命令：/状态 查看进行中的任务，/刷新 立即更新媒体库，/帮助 显示本说明。"
)


def _check_config() -> None:
    missing = [
        name
        for name, val in [
            ("QQ_BOT_APPID", APPID),
            ("QQ_BOT_APPSECRET", APPSECRET),
            ("MEDIARY_AGENT_TOKEN", MEDIARY_AGENT_TOKEN),
            ("ALLOWED_OPENIDS", os.environ.get("ALLOWED_OPENIDS", "")),
        ]
        if not val.strip()
    ]
    if missing:
        log.error("缺少必填环境变量: %s", ", ".join(missing))
        sys.exit(1)


# ---------------------------------------------------------------- Mediary API 客户端


class MediaryClient:
    def __init__(self, base_url: str, token: str) -> None:
        self._base = base_url
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def acquire(
        self, query: str, tmdb_id: int | None = None, media_type: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        body: dict[str, Any] = {"query": query}
        if tmdb_id is not None:
            body["tmdbId"] = tmdb_id
        if media_type:
            body["type"] = media_type
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base}/api/agent/acquire",
                json=body,
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                try:
                    data = await resp.json()
                except Exception:
                    data = {"message": f"HTTP {resp.status}（非 JSON 响应）"}
                return resp.status, data

    async def activity(self) -> dict[str, Any]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self._base}/api/agent/activity",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                return await resp.json()


# ---------------------------------------------------------------- 状态


class PendingChoice:
    """409 歧义待选：记录候选，等用户回序号。"""

    def __init__(self, query: str, candidates: list[dict[str, Any]]) -> None:
        self.query = query
        self.candidates = candidates
        self.created = time.monotonic()

    def expired(self) -> bool:
        return time.monotonic() - self.created > PENDING_TTL_S


class TrackedRun:
    """已受理的获取任务，后台轮询进度。"""

    def __init__(
        self, openid: str, title: str, workflow_run_id: str | None
    ) -> None:
        self.openid = openid
        self.title = title
        self.workflow_run_id = workflow_run_id


# ---------------------------------------------------------------- 机器人


class MediaryBot(botpy.Client):
    def __init__(self) -> None:
        intents = botpy.Intents(public_messages=True)
        super().__init__(intents=intents, is_sandbox=QQ_BOT_SANDBOX, timeout=30)
        self.mediary = MediaryClient(MEDIARY_API_URL, MEDIARY_AGENT_TOKEN)
        # openid -> 待选择的候选
        self._pending: dict[str, PendingChoice] = {}
        # workflow_run_id -> 跟踪中的任务
        self._runs: dict[str, TrackedRun] = {}

    async def on_ready(self) -> None:
        log.info("机器人已上线: %s", self.robot.name)

    # ------------------------------------------------------------ 消息入口

    async def on_c2c_message_create(self, message: C2CMessage) -> None:
        openid = message.author.user_openid
        if openid not in ALLOWED_OPENIDS:
            # 非白名单：直接忽略，不记录内容（WARNING 级，便于首次配置时拿到真实 openid）
            log.warning("忽略非白名单用户 %s 的消息", openid)
            return

        text = (message.content or "").strip()
        if not text:
            await self._reply(message, "请发送片名，或 /帮助 查看用法。")
            return

        try:
            if text in ("/帮助", "/help"):
                await self._reply(message, HELP_TEXT)
            elif text == "/状态":
                await self._handle_status(message)
            elif text == "/刷新":
                await self._trigger_strm_scan()
                await self._reply(message, "已触发媒体库刷新，稍等一两分钟后新内容就会出现在极影视里。")
            elif text.isdigit() and openid in self._pending:
                await self._handle_choice(message, int(text))
            else:
                await self._handle_acquire(message, text)
        except Exception:
            log.exception("处理消息出错")
            await self._reply(message, "内部出错，请稍后再试。")

    # ------------------------------------------------------------ 各处理器

    async def _handle_acquire(self, message: C2CMessage, query: str) -> None:
        status, data = await self.mediary.acquire(query)
        await self._respond_to_acquire(message, message.author.user_openid, query, status, data)

    async def _handle_choice(self, message: C2CMessage, index: int) -> None:
        openid = message.author.user_openid
        pending = self._pending.get(openid)
        if pending is None or pending.expired():
            self._pending.pop(openid, None)
            await self._reply(message, "选择已过期，请重新发送片名。")
            return
        if not (1 <= index <= len(pending.candidates)):
            await self._reply(message, f"序号超出范围，请输入 1-{len(pending.candidates)}。")
            return
        cand = pending.candidates[index - 1]
        self._pending.pop(openid, None)
        status, data = await self.mediary.acquire(
            pending.query, tmdb_id=cand["tmdbId"], media_type=cand.get("type")
        )
        await self._respond_to_acquire(message, openid, cand["title"], status, data)

    async def _respond_to_acquire(
        self,
        message: C2CMessage,
        openid: str,
        title: str,
        status: int,
        data: dict[str, Any],
    ) -> None:
        if status == 200:
            matched = (data.get("matched") or {}).get("title") or title
            run_id = data.get("workflowRunId")
            st = data.get("status")
            if st == "already_tracked":
                await self._reply(message, f"《{matched}》已在媒体库追踪中。")
                return
            await self._reply(message, f"已开始获取《{matched}》，完成后会通知你。")
            if run_id:
                self._runs[run_id] = TrackedRun(openid, matched, run_id)
                asyncio.create_task(self._poll_run(run_id))
            return
        if status == 409:
            candidates = data.get("candidates") or []
            if not candidates:
                await self._reply(message, f"《{title}》匹配歧义但未返回候选，请换个关键词。")
                return
            self._pending[openid] = PendingChoice(title, candidates)
            lines = [f"《{title}》有多个匹配，请回复序号选择："]
            for i, c in enumerate(candidates, 1):
                year = c.get("year") or "?"
                kind = "电影" if c.get("type") == "movie" else "剧集"
                lines.append(f"{i}. {c.get('title')}（{year}，{kind}）")
            await self._reply(message, "\n".join(lines))
            return
        if status == 404:
            await self._reply(message, data.get("message") or f"没找到《{title}》，换个关键词试试。")
            return
        if status == 401:
            await self._reply(message, "Mediary Scout 鉴权失败，请检查 MEDIARY_AGENT_TOKEN。")
            return
        await self._reply(
            message, data.get("message") or f"请求失败（HTTP {status}），请稍后再试。"
        )

    async def _handle_status(self, message: C2CMessage) -> None:
        openid = message.author.user_openid
        mine = [r for r in self._runs.values() if r.openid == openid]
        if not mine:
            await self._reply(message, "当前没有进行中的获取任务。")
            return
        lines = ["进行中的任务："] + [f"- 《{r.title}》" for r in mine]
        await self._reply(message, "\n".join(lines))

    # ------------------------------------------------------------ 进度轮询

    async def _poll_run(self, run_id: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            return
        elapsed = 0
        try:
            while elapsed < POLL_TIMEOUT_S:
                await asyncio.sleep(POLL_INTERVAL_S)
                elapsed += POLL_INTERVAL_S
                try:
                    data = await self.mediary.activity()
                except Exception:
                    log.warning("轮询 activity 失败", exc_info=True)
                    continue
                active_ids = {a.get("workflowRunId") for a in data.get("active", [])}
                if run_id in active_ids:
                    continue  # 仍在进行
                # 已从 active 消失：在 recent 通知里找结果
                note = self._find_notification(data.get("recent", []), run.title)
                if note:
                    body = note.get("body") or note.get("title") or ""
                    await self._push(run.openid, f"《{run.title}》{body}")
                else:
                    await self._push(run.openid, f"《{run.title}》获取流程已结束，请到媒体库确认结果。")
                await self._trigger_strm_scan()
                return
            await self._push(run.openid, f"《{run.title}》获取超时仍在进行，可到 Mediary Scout 活动页查看。")
        finally:
            self._runs.pop(run_id, None)

    @staticmethod
    def _find_notification(recent: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
        for n in recent:
            if title in (n.get("title") or "") or title in (n.get("body") or ""):
                return n
        return recent[0] if recent else None

    # ------------------------------------------------------------ 发消息

    async def _trigger_strm_scan(self) -> None:
        """通知 strm-sync 立即扫描一次，失败不影响主流程。"""
        if not STRM_TRIGGER_URL:
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    STRM_TRIGGER_URL, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    log.info("已触发 strm 扫描: HTTP %s", resp.status)
        except Exception:
            log.warning("触发 strm 扫描失败（不影响获取结果）", exc_info=True)

    async def _reply(self, message: C2CMessage, content: str) -> None:
        """被动回复（带 msg_id，不受主动消息额度限制）。"""
        await self.api.post_c2c_message(
            openid=message.author.user_openid,
            msg_type=0,
            content=content,
            msg_id=message.id,
        )

    async def _push(self, openid: str, content: str) -> None:
        """主动推送。QQ 对主动 C2C 消息有月度额度，超限会失败。"""
        try:
            await self.api.post_c2c_message(openid=openid, msg_type=0, content=content)
        except Exception:
            log.warning("主动推送失败（可能超出主动消息额度）", exc_info=True)


def main() -> None:
    _check_config()
    log.info("启动模式: %s", "沙箱" if QQ_BOT_SANDBOX else "正式")
    bot = MediaryBot()
    bot.run(appid=APPID, secret=APPSECRET)


if __name__ == "__main__":
    main()
