"""Playwright 浏览器单例管理"""
import asyncio
from typing import Optional

from playwright.async_api import Browser, Playwright, async_playwright

from .exceptions import BrowserNotAvailableError


class BrowserManager:
    """Playwright 浏览器单例（同步使用，不并发）"""

    _instance: Optional["BrowserManager"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    async def get_browser(self) -> Browser:
        """获取浏览器实例（懒初始化）。

        浏览器连接绑定在创建它的事件循环上：如果之后在另一个 loop 里复用
        （例如测试每个用例各跑一次 asyncio.run），连接会失效并报
        "'NoneType' object has no attribute 'send'"。因此这里检测 loop 变化，
        换了 loop 就丢弃旧连接、在新 loop 里重新启动。
        """
        loop = asyncio.get_running_loop()
        if self._browser is not None and self._loop is not loop:
            # 不能 await 已关闭 loop 上的连接，直接丢弃引用
            self._browser = None
            self._playwright = None
        if self._browser is None or not self._browser.is_connected():
            await self._start_browser()
            self._loop = loop
        return self._browser

    async def _start_browser(self):
        """启动 Playwright"""
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
        except Exception as e:
            raise BrowserNotAvailableError(f"Failed to start browser: {e}")

    async def close(self):
        """关闭浏览器"""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        self._loop = None
