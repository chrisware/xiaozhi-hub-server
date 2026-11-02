"""管理后台的 Grok Bot 账号管理：登录流程、Bot 列表、退出登录。

登录是 PKCE：后台生成登录地址给页面展示，用户在浏览器里批准，这里后台轮询直到拿到 token 并写入凭证文件。
provider（grok_bot）只读这个文件，文件变了会自动重读，所以登录后不用重启服务。
所有方法都在管理后台所在的事件循环里调用；网关调用（列 Bot）用自己的客户端实例，和 provider 的互不影响。
"""

import asyncio
import time
from pathlib import Path
from typing import List, Optional

from core.agent.grokbot_client import Credentials, GrokBotClient, GrokBotError, LoginFlow
from core.utils import language

LOGIN_TIMEOUT = 900  # 和桌面 App 的 150 次轮询差不多


def summarize_agent(a: dict) -> dict:
    return {
        "id": str(a.get("id") or ""),
        "name": str(a.get("name") or a.get("title") or ""),
        "group": bool(a.get("isGroup")),
        "hidden": bool(a.get("isHiddenFromSidebar") or a.get("isRemovedFromSidebar")),
        "busy": bool(a.get("isRunning") or a.get("isRunningTurn")),
        "preview": str(a.get("lastMessagePreview") or "")[:60],
    }


class GrokBotAdmin:
    def __init__(self, cred_path: Path = None, logger=None, backend: str = None, config: dict = None):
        self.creds = Credentials(cred_path)
        self.logger = logger
        self.backend = backend  # 只在测试时指向假服务
        self.config = config or {}  # 网关运行配置：只用来选后台语言，不读写配置值
        self._client: Optional[GrokBotClient] = None
        self._approved = False  # 这次登录已经拿到 token
        self._flow: Optional[LoginFlow] = None
        self._task: Optional[asyncio.Task] = None
        self._result: Optional[dict] = None  # 上一次登录的结果 {"ok": bool, "note": str, "at": 时间}
        self._bots: Optional[List[dict]] = None
        self._bots_at = 0.0
        self._bots_error = ""

    def _msg(self, en: str, zh: str) -> str:
        return language.msg(self.config, en, zh)

    def _log(self, level: str, msg: str) -> None:
        if self.logger:
            getattr(self.logger, level)(msg)

    def client(self) -> GrokBotClient:
        if self._client is None:
            kwargs = {"backend": self.backend} if self.backend else {}
            self._client = GrokBotClient(self.creds, logger=self.logger, **kwargs)
        return self._client

    # ---------- 状态 ----------

    @property
    def login_pending(self) -> bool:
        """还在等浏览器里批准。拿到 token 后任务还会去读 Bot 列表（可能要唤醒云端电脑），那段不算等待。"""
        return self._task is not None and not self._task.done() and not self._approved

    def status(self) -> dict:
        return {
            "account": self.creds.status(),
            "login": {
                "pending": self.login_pending,
                "url": self._flow.url if self.login_pending and self._flow else "",
                "started_at": self._flow.started_at if self.login_pending and self._flow else 0,
                "result": self._result,
            },
            "bots": self._bots,
            "bots_at": self._bots_at,
            "bots_error": self._bots_error,
        }

    # ---------- 登录 ----------

    async def start_login(self) -> str:
        """开始一次登录，返回要在浏览器里打开的地址。已经在等批准时返回同一个地址。"""
        if self.login_pending and self._flow:
            return self._flow.url
        self._flow = self.client().start_login()
        self._result = None
        self._approved = False
        self._task = asyncio.ensure_future(self._poll(self._flow))
        self._log("info", "管理后台开始 Grok Bot 登录，等待浏览器里批准")
        return self._flow.url

    async def _poll(self, flow: LoginFlow) -> None:
        try:
            ok = await self.client().poll_login(flow, timeout=LOGIN_TIMEOUT)
            if ok:
                self._approved = True
                self._result = {"ok": True, "note": self._msg("Signed in", "登录成功"), "at": time.time()}
                self._log("info", "Grok Bot 登录成功")
                self.client().conn = None
                try:
                    await self.fetch_bots()
                except GrokBotError as e:
                    self._log("warning", f"登录后读取 Bot 列表失败：{e.detail}")
            else:
                self._result = {"ok": False,
                                "note": self._msg("Not approved in the browser, or the wait timed out",
                                                  "没有在浏览器里批准，或者等待超时"),
                                "at": time.time()}
        except asyncio.CancelledError:
            if not self._approved:
                self._result = {"ok": False, "note": self._msg("Cancelled", "已取消"), "at": time.time()}
            raise
        except GrokBotError as e:
            self._result = {"ok": False, "note": self._msg(f"Sign-in failed: {e.detail}",
                                                           f"登录失败：{e.detail}"),
                            "at": time.time()}
            self._log("warning", f"Grok Bot 登录失败：{e.detail}")
        except Exception as e:  # noqa: BLE001 —— 后台任务，出错要让页面看得到
            self._result = {"ok": False, "note": self._msg(f"Sign-in error: {e!r}", f"登录出错：{e!r}"),
                            "at": time.time()}
            self._log("error", f"Grok Bot 登录出错：{e!r}")

    async def cancel_login(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def logout(self) -> None:
        """删掉本机凭证。服务端的会话不会撤销（没有可用的登出接口）。"""
        await self.cancel_login()
        self.creds.clear()
        if self._client is not None:
            self._client.conn = None
        self._bots, self._bots_at, self._bots_error = None, 0.0, ""
        self._log("info", "已退出 Grok Bot 登录（删除本机凭证）")

    # ---------- Bot 列表 ----------

    async def fetch_bots(self) -> List[dict]:
        """从云端读 Bot 列表（会唤醒休眠的云端电脑）。失败时抛 GrokBotError，并把原因记在 bots_error。"""
        try:
            agents = await self.client().list_agents()
        except GrokBotError as e:
            self._bots_error = e.detail
            raise
        self._bots = [summarize_agent(a) for a in agents if a.get("id") and not a.get("isGroup")]
        self._bots_at = time.time()
        self._bots_error = ""
        return self._bots

    async def close(self) -> None:
        await self.cancel_login()
        if self._client is not None:
            await self._client.close()
