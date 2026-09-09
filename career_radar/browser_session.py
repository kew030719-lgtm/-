from __future__ import annotations

import asyncio
import os
from typing import Any


class BrowserSessionError(RuntimeError):
    pass


class BossLoginSession:
    """Owns a user-driven BOSS login window and keeps its state in memory only."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._storage_state: dict[str, Any] | None = None
        self._confirmed = False
        self._mode = "window" if os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY") else "preview"

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self._page and not self._page.is_closed():
                await self._page.bring_to_front()
                return self.status()
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._mode = "window" if os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY") else "preview"
                self._browser = await self._playwright.chromium.launch(headless=self._mode != "window")
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()
                await self._page.goto(
                    "https://www.zhipin.com/web/user/?ka=header-login",
                    wait_until="domcontentloaded",
                    timeout=30_000,
                )
                await self._page.wait_for_timeout(1_500)
            except Exception as exc:
                await self._close_window()
                raise BrowserSessionError(f"无法打开 BOSS 登录窗口：{exc}") from exc
            self._confirmed = False
            return self.status()

    async def screenshot(self) -> bytes:
        async with self._lock:
            if not self._page or self._page.is_closed():
                raise BrowserSessionError("登录页面不存在，请先打开登录页面")
            return await self._page.screenshot(type="png", full_page=False)

    async def confirm(self) -> dict[str, Any]:
        async with self._lock:
            if not self._context or not self._page or self._page.is_closed():
                raise BrowserSessionError("登录窗口不存在，请先打开登录窗口")
            current_url = self._page.url
            if not current_url.startswith("https://www.zhipin.com/"):
                raise BrowserSessionError("登录窗口已离开 BOSS 直聘域名")
            if "/web/user" in current_url or "login" in current_url.lower():
                raise BrowserSessionError("登录页面尚未跳转，请先完成登录")
            self._storage_state = await self._context.storage_state()
            boss_cookies = [
                cookie for cookie in self._storage_state.get("cookies", [])
                if cookie.get("domain", "").lstrip(".").endswith("zhipin.com")
            ]
            if not boss_cookies:
                raise BrowserSessionError("尚未检测到 BOSS 会话，请先在登录窗口完成登录")
            self._confirmed = True
            await self._close_window(keep_state=True)
            return self.status()

    def storage_state(self) -> dict[str, Any] | None:
        return self._storage_state if self._confirmed else None

    def status(self) -> dict[str, Any]:
        window_open = bool(self._page and not self._page.is_closed())
        return {
            "window_open": window_open,
            "confirmed": self._confirmed,
            "session_in_memory": self._storage_state is not None,
            "mode": self._mode,
        }

    async def clear(self) -> None:
        async with self._lock:
            self._storage_state = None
            self._confirmed = False
            await self._close_window()

    async def close(self) -> None:
        async with self._lock:
            await self._close_window()
            self._storage_state = None
            self._confirmed = False

    async def _close_window(self, *, keep_state: bool = False) -> None:
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        if not keep_state:
            self._storage_state = None
