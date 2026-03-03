from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, async_playwright

from src.config import Settings
from src.platforms import get_platform_adapter

try:
    from browser_use import Agent as BrowserUseAgent
    from browser_use import Browser as BrowserUseBrowser
except Exception:  # pragma: no cover - import guard for optional API changes
    BrowserUseAgent = None
    BrowserUseBrowser = None

try:
    from browser_use import BrowserConfig as BrowserUseConfig
except Exception:  # pragma: no cover
    BrowserUseConfig = None


@dataclass
class BrowserOperationResult:
    status: str
    live_url: str
    note: str


async def upload_files(
    page: Page,
    image_paths: list[str],
    input_selectors: list[str],
    upload_keywords: list[str],
) -> bool:
    files = [str(Path(path).resolve()) for path in image_paths]
    for selector in input_selectors:
        locator = page.locator(selector)
        count = await locator.count()
        if count == 0:
            continue
        for idx in range(count):
            with contextlib.suppress(Exception):
                await locator.nth(idx).set_input_files(files, timeout=4_000)
                return True

    for keyword in upload_keywords:
        target = page.get_by_text(keyword, exact=False)
        if await target.count() == 0:
            continue
        for idx in range(await target.count()):
            with contextlib.suppress(Exception):
                async with page.expect_file_chooser(timeout=4_000) as chooser_info:
                    await target.nth(idx).click(timeout=3_000)
                chooser = await chooser_info.value
                await chooser.set_files(files)
                return True
    return False


class BrowserOperator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._live_sessions: list[tuple[Any, BrowserContext]] = []

    async def run(
        self,
        platform: str,
        title: str,
        content: str,
        image_paths: list[str],
        browser_llm: Any | None = None,
    ) -> BrowserOperationResult:
        if self.settings.mock_mode or self.settings.browser_mode.lower() == "mock":
            return BrowserOperationResult(
                status="ready",
                live_url="",
                note="Mock 模式：已模拟打开发布页并停在发布按钮前，请在真实模式验证。",
            )

        adapter = get_platform_adapter(platform)

        if self.settings.browser_use_enabled:
            with contextlib.suppress(Exception):
                result = await self._run_with_browser_use(adapter, title, content, image_paths, browser_llm)
                if result.status in {"ready", "need_login"}:
                    return result

        if self.settings.is_cloud_mode:
            return BrowserOperationResult(
                status="failed",
                live_url="",
                note="Cloud 模式下 browser-use 执行失败，请检查 browser-use cloud 配置。",
            )

        return await self._run_with_playwright(adapter, title, content, image_paths)

    async def _run_with_browser_use(
        self,
        adapter: Any,
        title: str,
        content: str,
        image_paths: list[str],
        browser_llm: Any | None,
    ) -> BrowserOperationResult:
        if BrowserUseAgent is None or BrowserUseBrowser is None:
            raise RuntimeError("browser-use not available")
        if browser_llm is None:
            raise RuntimeError("browser-use requires an LLM instance")

        browser = self._create_browser_use_browser()
        task = adapter.browser_task(title=title, content=content, image_count=len(image_paths))
        agent = BrowserUseAgent(task=task, llm=browser_llm, browser=browser)
        result = await agent.run()
        live_url = self._extract_live_url(result)
        note = (
            "browser-use 已执行到发布前。请人工核对标题、正文、图片后手动点击发布。"
            " 系统已禁止自动点击发布。"
        )
        return BrowserOperationResult(status="ready", live_url=live_url, note=note)

    async def _run_with_playwright(
        self,
        adapter: Any,
        title: str,
        content: str,
        image_paths: list[str],
    ) -> BrowserOperationResult:
        user_data_dir = self.settings.browser_user_data_dir
        if not user_data_dir:
            return BrowserOperationResult(
                status="failed",
                live_url="",
                note="Real Browser 模式缺少 BROWSER_USER_DATA_DIR，无法复用登录态。",
            )

        profile_arg = []
        if self.settings.browser_profile_directory:
            profile_arg = [f"--profile-directory={self.settings.browser_profile_directory}"]

        playwright = await async_playwright().start()
        context: BrowserContext | None = None
        keep_session_open = False
        try:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                executable_path=self.settings.browser_executable_path or None,
                headless=self.settings.browser_headless,
                args=profile_arg,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(adapter.creator_center_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2_000)

            if await self._need_login(page):
                return BrowserOperationResult(
                    status="need_login",
                    live_url="",
                    note="检测到未登录，请先在弹出的浏览器中登录创作中心，然后重新发起任务。",
                )

            opened = await self._open_publish_entry(page, adapter.publish_entry_keywords, adapter.fallback_publish_entry_selectors)
            if not opened:
                return BrowserOperationResult(
                    status="failed",
                    live_url="",
                    note="未找到发布入口，请手动进入发布页后重试。",
                )

            await page.wait_for_timeout(2_000)
            await self._fill_text(page, adapter.title_selectors, title)
            await self._fill_text(page, adapter.content_selectors, content)

            uploaded = await upload_files(
                page=page,
                image_paths=image_paths,
                input_selectors=adapter.upload_input_selectors,
                upload_keywords=adapter.upload_trigger_keywords,
            )
            if not uploaded:
                return BrowserOperationResult(
                    status="failed",
                    live_url="",
                    note="未能自动上传图片，请确认页面有可用上传控件后再试。",
                )

            publish_button_found = await self._find_publish_button(
                page,
                adapter.publish_button_keywords,
                adapter.publish_button_selectors,
            )
            if not publish_button_found:
                return BrowserOperationResult(
                    status="failed",
                    live_url="",
                    note="未识别到发布按钮，无法确认已到最终发布前步骤。",
                )

            note = (
                "浏览器已就绪，已自动填充标题/正文并上传图片，当前停在发布按钮前。"
                "请你人工核对后手动点击发布。"
            )
            keep_session_open = self.settings.browser_keep_alive
            if keep_session_open and context is not None:
                self._live_sessions.append((playwright, context))
            return BrowserOperationResult(status="ready", live_url="", note=note)
        except PlaywrightTimeoutError:
            return BrowserOperationResult(status="failed", live_url="", note="浏览器操作超时，请重试。")
        except Exception as exc:
            return BrowserOperationResult(status="failed", live_url="", note=f"浏览器执行失败: {exc}")
        finally:
            if context and not keep_session_open:
                with contextlib.suppress(Exception):
                    await context.close()
            if not keep_session_open:
                with contextlib.suppress(Exception):
                    await playwright.stop()

    def _create_browser_use_browser(self) -> Any:
        if BrowserUseBrowser is None:
            raise RuntimeError("browser-use Browser unavailable")

        kwargs = {
            "headless": self.settings.browser_headless,
            "keep_alive": self.settings.browser_keep_alive,
            "executable_path": self.settings.browser_executable_path or None,
            "user_data_dir": self.settings.browser_user_data_dir or None,
            "profile_directory": self.settings.browser_profile_directory or None,
            "cloud": self.settings.is_cloud_mode,
            "project_id": self.settings.browser_cloud_project_id or None,
        }
        clean_kwargs = {k: v for k, v in kwargs.items() if v not in ("", None)}
        with contextlib.suppress(TypeError):
            return BrowserUseBrowser(**clean_kwargs)

        if BrowserUseConfig is not None:
            with contextlib.suppress(TypeError):
                config = BrowserUseConfig(**clean_kwargs)
                return BrowserUseBrowser(config=config)

        return BrowserUseBrowser()

    def _extract_live_url(self, result: Any) -> str:
        candidates = [
            getattr(result, "live_url", ""),
            getattr(result, "browser_live_url", ""),
            self.settings.browser_cloud_live_url,
        ]
        for value in candidates:
            if value:
                return str(value)
        if isinstance(result, dict):
            for key in ("live_url", "browser_live_url"):
                if result.get(key):
                    return str(result[key])
        return ""

    async def _need_login(self, page: Page) -> bool:
        for keyword in ("登录", "扫码登录", "手机号登录"):
            with contextlib.suppress(Exception):
                locator = page.get_by_text(keyword, exact=False)
                if await locator.count() > 0:
                    return True
        return False

    async def _open_publish_entry(self, page: Page, keywords: list[str], selectors: list[str]) -> bool:
        for keyword in keywords:
            locator = page.get_by_text(keyword, exact=False)
            count = await locator.count()
            if count == 0:
                continue
            for idx in range(count):
                with contextlib.suppress(Exception):
                    await locator.nth(idx).click(timeout=3_000)
                    return True
        for selector in selectors:
            with contextlib.suppress(Exception):
                locator = page.locator(selector)
                if await locator.count() > 0:
                    await locator.first.click(timeout=3_000)
                    return True
        return False

    async def _fill_text(self, page: Page, selectors: list[str], content: str) -> bool:
        content = content.strip()
        if not content:
            return False
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            with contextlib.suppress(Exception):
                await locator.first.fill(content, timeout=3_000)
                return True

        textboxes = page.get_by_role("textbox")
        total = await textboxes.count()
        for idx in range(total):
            with contextlib.suppress(Exception):
                await textboxes.nth(idx).fill(content, timeout=2_000)
                return True
        return False

    async def _find_publish_button(self, page: Page, keywords: list[str], selectors: list[str]) -> bool:
        for keyword in keywords:
            locator = page.get_by_text(keyword, exact=False)
            if await locator.count() > 0:
                return True
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() > 0:
                return True
        return False
