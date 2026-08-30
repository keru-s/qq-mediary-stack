"""strm-sync: 定时遍历 OpenList 的 Strm 驱动目录，触发本地 .strm 文件生成。

背景：OpenList 的 Strm 驱动只在「目录被访问时」生成本地 strm 文件，
不支持定时/监听网盘变更自动生成。Mediary Scout 转存直接走夸克 API，
OpenList 感知不到新文件。本服务用 OpenList 的 fs/list API 递归遍历
Strm 驱动的挂载路径，等价于「访问目录」，从而触发生成。

频率由 SCAN_INTERVAL_S 控制（默认 1800s）。遍历是夸克 API 调用，
DIR_DELAY_S 控制每个目录之间的间隔，避免高频触发网盘风控。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s strm-sync: %(message)s",
    force=True,
)
log = logging.getLogger("strm-sync")

OPENLIST_URL = os.environ.get("OPENLIST_URL", "http://openlist:5244").rstrip("/")
OPENLIST_USER = os.environ.get("OPENLIST_USER", "admin").strip()
OPENLIST_PASSWORD = os.environ.get("OPENLIST_PASSWORD", "").strip()
# Strm 驱动在 OpenList 里的挂载路径（不是夸克盘的路径）
STRM_PATH = os.environ.get("STRM_PATH", "/strm").strip()
SCAN_INTERVAL_S = int(os.environ.get("SCAN_INTERVAL_S", "21600"))
DIR_DELAY_S = float(os.environ.get("DIR_DELAY_S", "1"))
# 即时触发端口（仅容器网络内部使用，不映射到宿主）
TRIGGER_PORT = int(os.environ.get("TRIGGER_PORT", "8000"))

_wake = threading.Event()


class _TriggerHandler(BaseHTTPRequestHandler):
    """POST /scan → 唤醒主循环立即扫描一次。qqbot 在获取完成时调用。"""

    def do_POST(self) -> None:
        if self.path.rstrip("/") == "/scan":
            _wake.set()
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b'{"status":"scheduled"}')
            log.info("收到即时扫描触发")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args) -> None:  # 静音 http.server 的访问日志
        pass


def _check_config() -> None:
    if not OPENLIST_PASSWORD:
        log.error("缺少必填环境变量: OPENLIST_PASSWORD（OpenList 管理员密码）")
        sys.exit(1)


class OpenListClient:
    def __init__(self) -> None:
        self._token: str | None = None

    def _login(self) -> None:
        resp = requests.post(
            f"{OPENLIST_URL}/api/auth/login",
            json={"username": OPENLIST_USER, "password": OPENLIST_PASSWORD},
            timeout=30,
        )
        data = resp.json()
        if data.get("code") != 200:
            raise RuntimeError(f"OpenList 登录失败: {data.get('message')}")
        self._token = data["data"]["token"]

    def _request(self, path: str, payload: dict) -> dict:
        for attempt in range(2):
            if self._token is None:
                self._login()
            resp = requests.post(
                f"{OPENLIST_URL}{path}",
                json=payload,
                headers={"Authorization": self._token or ""},
                timeout=60,
            )
            data = resp.json()
            if data.get("code") == 401 and attempt == 0:
                self._token = None  # token 过期，重新登录后重试一次
                continue
            return data
        return data

    def list_dir(self, path: str) -> list[dict]:
        data = self._request(
            "/api/fs/list",
            {"path": path, "page": 1, "per_page": 0, "refresh": True},
        )
        if data.get("code") != 200:
            raise RuntimeError(f"列目录失败 {path}: {data.get('message')}")
        return (data.get("data") or {}).get("content") or []


def walk(client: OpenListClient, path: str, stats: dict) -> None:
    """递归遍历，触发 Strm 驱动的本地文件生成。"""
    try:
        entries = client.list_dir(path)
    except Exception:
        log.exception("遍历失败: %s", path)
        return
    for e in entries:
        if e.get("is_dir"):
            stats["dirs"] += 1
            time.sleep(DIR_DELAY_S)
            walk(client, f"{path}/{e['name']}", stats)
        else:
            stats["files"] += 1


def main() -> None:
    _check_config()
    client = OpenListClient()
    server = ThreadingHTTPServer(("0.0.0.0", TRIGGER_PORT), _TriggerHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info(
        "启动：目标 %s，路径 %s，兜底扫描间隔 %ds，即时触发端口 %d",
        OPENLIST_URL, STRM_PATH, SCAN_INTERVAL_S, TRIGGER_PORT,
    )
    while True:
        stats = {"dirs": 0, "files": 0}
        started = time.monotonic()
        walk(client, STRM_PATH, stats)
        log.info(
            "扫描完成：%d 个目录、%d 个文件，耗时 %.1fs",
            stats["dirs"], stats["files"], time.monotonic() - started,
        )
        # 等待下一次兜底扫描；收到触发则立即开始下一轮
        _wake.wait(timeout=SCAN_INTERVAL_S)
        _wake.clear()


if __name__ == "__main__":
    main()
