from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.async_api import (
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from src.config import Settings
from src.platforms import get_platform_adapter

try:
    from browser_use import Agent as BrowserUseAgent
    from browser_use import Browser as BrowserUseBrowser
except Exception:  # pragma: no cover
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
        count = await target.count()
        if count == 0:
            continue
        for idx in range(count):
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
        self._live_sessions: list[tuple[Any, BrowserContext, Any | None]] = []

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
                note="Mock mode: browser publish flow is simulated and paused before final publish.",
            )

        adapter = get_platform_adapter(platform)
        browser_use_detail = ""

        should_try_browser_use = self.settings.browser_use_enabled
        if should_try_browser_use and sys.platform == "win32" and self.settings.browser_mode.lower() == "real":
            should_try_browser_use = False
            browser_use_detail = (
                "browser-use skipped on Windows real mode "
                "(known multiprocessing pipe instability may stall Node D)."
            )

        if should_try_browser_use:
            try:
                result = await self._run_with_browser_use(adapter, title, content, image_paths, browser_llm)
                if result.status in {"ready", "need_login"}:
                    return result
                browser_use_detail = f"browser-use status={result.status}; note={result.note}"
            except Exception as exc:
                browser_use_detail = f"browser-use failed: {exc}"

        if self.settings.is_cloud_mode:
            note = "Cloud mode failed via browser-use. Please verify browser-use cloud configuration."
            if browser_use_detail:
                note = f"{note} detail={browser_use_detail}"
            return BrowserOperationResult(
                status="failed",
                live_url="",
                note=note,
            )

        playwright_result = await self._run_with_playwright(adapter, title, content, image_paths)
        if browser_use_detail and playwright_result.status == "failed":
            return BrowserOperationResult(
                status=playwright_result.status,
                live_url=playwright_result.live_url,
                note=f"{playwright_result.note} | {browser_use_detail}",
            )
        return playwright_result

    async def _run_with_browser_use(
        self,
        adapter: Any,
        title: str,
        content: str,
        image_paths: list[str],
        browser_llm: Any | None,
    ) -> BrowserOperationResult:
        return await asyncio.to_thread(
            self._run_browser_in_thread,
            adapter,
            title,
            content,
            image_paths,
            browser_llm,
        )

    def _run_browser_in_thread(
        self,
        adapter: Any,
        title: str,
        content: str,
        image_paths: list[str],
        browser_llm: Any | None,
    ) -> BrowserOperationResult:
        if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        timeout_sec = int(self.settings.browser_operation_timeout_sec or 240)
        if timeout_sec <= 0:
            timeout_sec = 240

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                asyncio.wait_for(
                    self._run_with_browser_use_async(adapter, title, content, image_paths, browser_llm),
                    timeout=timeout_sec,
                )
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"browser-use timed out after {timeout_sec}s") from exc
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            asyncio.set_event_loop(None)
            loop.close()

    async def _run_with_browser_use_async(
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
            "browser-use completed until pre-publish step. "
            "Please manually verify title/content/images and click publish yourself."
        )
        return BrowserOperationResult(status="ready", live_url=live_url, note=note)

    async def _run_with_playwright(
        self,
        adapter: Any,
        title: str,
        content: str,
        image_paths: list[str],
    ) -> BrowserOperationResult:
        if self._should_isolate_playwright_loop():
            return await asyncio.to_thread(
                self._run_playwright_in_thread,
                adapter,
                title,
                content,
                image_paths,
            )
        result = await self._run_with_playwright_async(adapter, title, content, image_paths)
        if (
            sys.platform == "win32"
            and result.status == "failed"
            and "subprocess not supported" in result.note.lower()
        ):
            return await asyncio.to_thread(
                self._run_playwright_in_thread,
                adapter,
                title,
                content,
                image_paths,
            )
        return result

    def _should_isolate_playwright_loop(self) -> bool:
        if sys.platform != "win32":
            return False
        with contextlib.suppress(RuntimeError):
            loop_name = type(asyncio.get_running_loop()).__name__.lower()
            if "proactor" in loop_name:
                return False
        return True

    def _run_playwright_in_thread(
        self,
        adapter: Any,
        title: str,
        content: str,
        image_paths: list[str],
    ) -> BrowserOperationResult:
        if sys.platform == "win32" and hasattr(asyncio, "WindowsProactorEventLoopPolicy"):
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        timeout_sec = int(self.settings.browser_operation_timeout_sec or 240)
        if timeout_sec <= 0:
            timeout_sec = 240

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(
                asyncio.wait_for(
                    self._run_with_playwright_async(adapter, title, content, image_paths),
                    timeout=timeout_sec,
                )
            )
        except asyncio.TimeoutError:
            return BrowserOperationResult(
                status="failed",
                live_url="",
                note=f"Browser operation timed out after {timeout_sec}s.",
            )
        except Exception as exc:
            return BrowserOperationResult(status="failed", live_url="", note=f"Browser execution failed: {exc}")
        finally:
            with contextlib.suppress(Exception):
                loop.run_until_complete(loop.shutdown_asyncgens())
            asyncio.set_event_loop(None)
            loop.close()

    async def _run_with_playwright_async(
        self,
        adapter: Any,
        title: str,
        content: str,
        image_paths: list[str],
    ) -> BrowserOperationResult:
        await self._release_live_sessions()
        user_data_dir, user_data_dir_detail = self._resolve_user_data_dir()
        if not user_data_dir:
            return BrowserOperationResult(
                status="failed",
                live_url="",
                note=(
                    "Real browser mode requires a valid Chrome user data directory. "
                    "Please set BROWSER_USER_DATA_DIR or ensure LOCALAPPDATA is available."
                ),
            )

        profile_args: list[str] = []
        if self.settings.browser_profile_directory:
            profile_args = [f"--profile-directory={self.settings.browser_profile_directory}"]

        loop_name = type(asyncio.get_running_loop()).__name__
        playwright = None
        launched_browser = None
        context: BrowserContext | None = None
        keep_session_open = False
        active_user_data_dir = user_data_dir
        launch_recovery_note = ""
        temp_user_data_dir = ""
        try:
            playwright = await async_playwright().start()
            (
                context,
                active_user_data_dir,
                launch_recovery_note,
                temp_user_data_dir,
                launched_browser,
            ) = await self._launch_context_with_profile_recovery(
                playwright=playwright,
                user_data_dir=user_data_dir,
                profile_args=profile_args,
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(adapter.creator_center_url, wait_until="domcontentloaded")
            await page.wait_for_timeout(2_000)

            if await self._need_login_robust(page):
                login_wait_timeout = min(max(int(self.settings.browser_operation_timeout_sec or 240), 60), 300)
                page_after_login = await self._wait_for_manual_login(context, timeout_sec=login_wait_timeout)
                if page_after_login is None:
                    keep_session_open = True
                    if context is not None and playwright is not None:
                        self._live_sessions.append((playwright, context, launched_browser))
                    login_note = (
                        "Login required. Please login in the opened browser and retry. "
                        f"Waited {login_wait_timeout}s but login was not detected."
                    )
                    if launch_recovery_note:
                        login_note = f"{login_note} {launch_recovery_note}"
                    return BrowserOperationResult(
                        status="need_login",
                        live_url="",
                        note=login_note,
                    )
                page = page_after_login
                await page.goto(adapter.creator_center_url, wait_until="domcontentloaded")
                await page.wait_for_timeout(1_500)

            opened = await self._open_publish_entry(
                page,
                adapter.publish_entry_keywords,
                adapter.fallback_publish_entry_selectors,
            )
            if not opened:
                return BrowserOperationResult(
                    status="failed",
                    live_url="",
                    note="Publish entry not found. Open publish page manually then retry.",
                )

            await page.wait_for_timeout(2_000)
            await self._ensure_platform_publish_mode(page, adapter.name)
            await self._fill_text(page, adapter.title_selectors, title)
            await self._fill_text(page, adapter.content_selectors, content)

            uploaded = await upload_files(
                page=page,
                image_paths=image_paths,
                input_selectors=adapter.upload_input_selectors,
                upload_keywords=adapter.upload_trigger_keywords,
            )
            if not uploaded and adapter.name.lower() == "xhs":
                with contextlib.suppress(Exception):
                    await self._ensure_xhs_image_tab(page)
                    await page.wait_for_timeout(1_000)
                uploaded = await upload_files(
                    page=page,
                    image_paths=image_paths,
                    input_selectors=adapter.upload_input_selectors,
                    upload_keywords=adapter.upload_trigger_keywords,
                )
            if not uploaded:
                extra_hint = ""
                if adapter.name.lower() == "xhs":
                    hint = await self._detect_xhs_upload_hint(page)
                    if hint:
                        extra_hint = f" {hint}"
                return BrowserOperationResult(
                    status="failed",
                    live_url="",
                    note=f"Auto upload failed. Please verify upload controls on page.{extra_hint}",
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
                    note="Publish button not found, cannot confirm pre-publish state.",
                )

            note = (
                "Browser is ready. Title/content/images are filled and flow is paused before publish. "
                "Please verify manually and click publish yourself."
            )
            if launch_recovery_note:
                note = f"{note} {launch_recovery_note}"
            keep_session_open = self.settings.browser_keep_alive
            if keep_session_open and context is not None and playwright is not None:
                self._live_sessions.append((playwright, context, launched_browser))
            return BrowserOperationResult(status="ready", live_url="", note=note)
        except PlaywrightTimeoutError:
            return BrowserOperationResult(status="failed", live_url="", note="Browser operation timed out.")
        except NotImplementedError as exc:
            if sys.platform == "win32":
                note = (
                    "Browser subprocess not supported by current asyncio loop "
                    f"({loop_name}). Node D moved Playwright to isolated Proactor loop."
                )
                return BrowserOperationResult(status="failed", live_url="", note=f"{note} detail={exc}")
            return BrowserOperationResult(status="failed", live_url="", note=f"Browser execution failed: {exc}")
        except Exception as exc:
            detail = f"user_data_dir={active_user_data_dir}"
            if user_data_dir_detail:
                detail = f"{detail}; {user_data_dir_detail}"
            if launch_recovery_note:
                detail = f"{detail}; {launch_recovery_note}"
            return BrowserOperationResult(
                status="failed",
                live_url="",
                note=f"Browser execution failed: {exc} ({detail})",
            )
        finally:
            if context and not keep_session_open:
                with contextlib.suppress(Exception):
                    await context.close()
            if launched_browser is not None and not keep_session_open:
                with contextlib.suppress(Exception):
                    await launched_browser.close()
            if playwright is not None and not keep_session_open:
                with contextlib.suppress(Exception):
                    await playwright.stop()
            if temp_user_data_dir and not keep_session_open:
                with contextlib.suppress(Exception):
                    shutil.rmtree(temp_user_data_dir, ignore_errors=True)

    def _create_browser_use_browser(self) -> Any:
        if BrowserUseBrowser is None:
            raise RuntimeError("browser-use Browser unavailable")

        resolved_user_data_dir, _ = self._resolve_user_data_dir()
        kwargs = {
            "headless": self.settings.browser_headless,
            "keep_alive": self.settings.browser_keep_alive,
            "executable_path": self.settings.browser_executable_path or None,
            "user_data_dir": resolved_user_data_dir or None,
            "profile_directory": self.settings.browser_profile_directory or None,
            "cloud": self.settings.is_cloud_mode,
            "project_id": self.settings.browser_cloud_project_id or None,
        }
        clean_kwargs = {key: value for key, value in kwargs.items() if value not in ("", None)}
        with contextlib.suppress(TypeError):
            return BrowserUseBrowser(**clean_kwargs)

        if BrowserUseConfig is not None:
            with contextlib.suppress(TypeError):
                config = BrowserUseConfig(**clean_kwargs)
                return BrowserUseBrowser(config=config)

        return BrowserUseBrowser()

    def _resolve_user_data_dir(self) -> tuple[str, str]:
        configured = (self.settings.browser_user_data_dir or "").strip()
        candidates: list[tuple[str, str]] = []
        notes: list[str] = []

        if configured:
            normalized = configured
            if "<YOUR_USER>" in normalized:
                username = (os.environ.get("USERNAME") or "").strip()
                if username:
                    normalized = normalized.replace("<YOUR_USER>", username)
                    notes.append("replaced <YOUR_USER> with current username")
            normalized = os.path.expandvars(os.path.expanduser(normalized))
            candidates.append(("configured", normalized))

        local_app_data = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            default_dir = str(Path(local_app_data) / "Google" / "Chrome" / "User Data")
            candidates.append(("default", default_dir))

        dedup: list[tuple[str, str]] = []
        seen: set[str] = set()
        for source, candidate in candidates:
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            dedup.append((source, candidate))

        for source, candidate in dedup:
            if Path(candidate).exists():
                if source == "default" and configured and candidate != configured:
                    return candidate, "falling back to LOCALAPPDATA Chrome profile"
                return candidate, "; ".join(notes)

        for source, candidate in dedup:
            parent = Path(candidate).parent
            if parent.exists():
                if source == "configured":
                    return candidate, "; ".join(notes)
                return candidate, "using LOCALAPPDATA Chrome profile path"

        return "", "unable to resolve a usable Chrome user data directory"

    async def _launch_context_with_profile_recovery(
        self,
        playwright: Any,
        user_data_dir: str,
        profile_args: list[str],
    ) -> tuple[BrowserContext, str, str, str, Any | None]:
        configured_exec = (self.settings.browser_executable_path or "").strip() or None
        recovery_user_data_dir = self._get_recovery_profile_dir()

        exec_candidates: list[tuple[str, str | None]] = [("configured executable", configured_exec)]
        if configured_exec is not None:
            exec_candidates.append(("playwright bundled chromium", None))
        else:
            exec_candidates[0] = ("playwright bundled chromium (default)", None)

        profile_candidates = [
            ("configured profile", user_data_dir, profile_args),
            ("recovery profile", recovery_user_data_dir, []),
        ]
        if sys.platform == "win32":
            sandbox_candidates: list[tuple[str, bool]] = [
                ("sandbox enabled", True),
                ("no sandbox", False),
            ]
        else:
            sandbox_candidates = [("no sandbox", False)]

        attempt_errors: list[str] = []
        for profile_name, profile_dir, launch_args in profile_candidates:
            for sandbox_name, sandbox_enabled in sandbox_candidates:
                for exec_name, exec_path in exec_candidates:
                    try:
                        context = await playwright.chromium.launch_persistent_context(
                            user_data_dir=profile_dir,
                            executable_path=exec_path,
                            headless=self.settings.browser_headless,
                            args=launch_args,
                            chromium_sandbox=sandbox_enabled,
                        )
                        note_parts: list[str] = []
                        if profile_name != "configured profile":
                            note_parts.append(
                                "Primary Chrome profile is unavailable (likely in use by another Chrome process). "
                                f"Switched to reusable recovery profile: {recovery_user_data_dir}"
                            )
                        if exec_name not in {"configured executable", "playwright bundled chromium (default)"}:
                            note_parts.append("Configured Chrome executable failed; switched to Playwright Chromium.")
                        if sandbox_name != "no sandbox":
                            note_parts.append("Enabled Chromium sandbox for Windows stability.")
                        return context, profile_dir, " ".join(note_parts).strip(), "", None
                    except Exception as exc:
                        attempt_errors.append(
                            f"{profile_name} + {sandbox_name} + {exec_name}: {self._summarize_exception(exc)}"
                        )
                        if not self._is_profile_locked_error(exc):
                            continue

        ephemeral_errors: list[str] = []
        for sandbox_name, sandbox_enabled in sandbox_candidates:
            for exec_name, exec_path in exec_candidates:
                try:
                    browser = await playwright.chromium.launch(
                        executable_path=exec_path,
                        headless=self.settings.browser_headless,
                        chromium_sandbox=sandbox_enabled,
                    )
                    context = await browser.new_context()
                    note_parts = [
                        "Persistent profile launch failed; switched to non-persistent browser context."
                    ]
                    if exec_name not in {"configured executable", "playwright bundled chromium (default)"}:
                        note_parts.append("Configured Chrome executable failed; switched to Playwright Chromium.")
                    if sandbox_name != "no sandbox":
                        note_parts.append("Enabled Chromium sandbox for Windows stability.")
                    note_parts.append("You may need to login again in this run.")
                    return context, "non_persistent", " ".join(note_parts), "", browser
                except Exception as exc:
                    ephemeral_errors.append(
                        f"non-persistent + {sandbox_name} + {exec_name}: {self._summarize_exception(exc)}"
                    )

        raise RuntimeError(
            "Failed to launch browser for Node D after trying configured/recovery profiles and non-persistent "
            "fallbacks. "
            + " | ".join([*attempt_errors, *ephemeral_errors])
        )

    async def _release_live_sessions(self) -> None:
        if not self._live_sessions:
            return
        sessions = list(self._live_sessions)
        self._live_sessions.clear()
        for playwright, context, browser in sessions:
            with contextlib.suppress(Exception):
                await context.close()
            if browser is not None:
                with contextlib.suppress(Exception):
                    await browser.close()
            with contextlib.suppress(Exception):
                await playwright.stop()
        await asyncio.sleep(1)

    def _summarize_exception(self, exc: Exception) -> str:
        text = " ".join(str(exc).splitlines())
        text = " ".join(text.split())
        if len(text) > 260:
            text = text[:260] + "..."
        return text

    def _is_profile_locked_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        indicators = (
            "target page, context or browser has been closed",
            "exitcode=21",
            "user data directory is already in use",
            "profile appears to be in use",
            "singletonlock",
        )
        return any(indicator in text for indicator in indicators)

    def _get_recovery_profile_dir(self) -> str:
        local_app_data = (os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            recovery_dir = Path(local_app_data) / "autonomous-content-agent" / "chrome-recovery-profile"
        else:
            recovery_dir = Path.cwd() / ".chrome-recovery-profile"
        recovery_dir.mkdir(parents=True, exist_ok=True)
        return str(recovery_dir)

    def _extract_live_url(self, result: Any) -> str:
        candidates = [
            getattr(result, "live_url", ""),
            getattr(result, "browser_live_url", ""),
            self.settings.browser_cloud_live_url,
        ]
        for candidate in candidates:
            if candidate:
                return str(candidate)
        if isinstance(result, dict):
            for key in ("live_url", "browser_live_url"):
                value = result.get(key)
                if value:
                    return str(value)
        return ""

    async def _need_login(self, page: Page) -> bool:
        for keyword in ("登录", "扫码登录", "手机号登录"):
            with contextlib.suppress(Exception):
                locator = page.get_by_text(keyword, exact=False)
                if await locator.count() > 0:
                    return True
        return False

    async def _need_login_robust(self, page: Page) -> bool:
        with contextlib.suppress(Exception):
            current_url = (page.url or "").lower()
            if any(token in current_url for token in ("login", "signin", "passport")):
                return True
        for keyword in ("登录", "扫码登录", "手机号登录", "Sign in", "Log in"):
            with contextlib.suppress(Exception):
                locator = page.get_by_text(keyword, exact=False)
                if await locator.count() > 0:
                    return True
        with contextlib.suppress(Exception):
            if await page.locator("input[type='password']").count() > 0:
                return True
        return False

    async def _wait_for_manual_login(self, context: BrowserContext, timeout_sec: int) -> Page | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout_sec, 1)
        while loop.time() < deadline:
            for page in list(context.pages):
                if not await self._need_login_robust(page):
                    return page
            await asyncio.sleep(2)
        return None

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

    async def _ensure_platform_publish_mode(self, page: Page, platform_name: str) -> None:
        if platform_name.lower() == "xhs":
            await self._ensure_xhs_image_tab(page)

    async def _ensure_xhs_image_tab(self, page: Page) -> bool:
        if await self._looks_like_xhs_image_mode(page):
            return True

        selectors = [
            "button:has-text('图文')",
            "[role='tab']:has-text('图文')",
            "div:has-text('图文')",
            "span:has-text('图文')",
            "a:has-text('图文')",
            "text=图文",
        ]
        for _ in range(2):
            for selector in selectors:
                with contextlib.suppress(Exception):
                    locator = page.locator(selector)
                    if await locator.count() == 0:
                        continue
                    await locator.first.click(timeout=2_500)
                    await page.wait_for_timeout(600)
                    if await self._looks_like_xhs_image_mode(page):
                        return True
        return await self._looks_like_xhs_image_mode(page)

    async def _looks_like_xhs_image_mode(self, page: Page) -> bool:
        selectors = [
            "input[accept*='image']",
            "text=上传图文",
            "text=添加图片",
            "text=选择图片",
        ]
        for selector in selectors:
            with contextlib.suppress(Exception):
                locator = page.locator(selector)
                if await locator.count() > 0:
                    return True
        return False

    async def _detect_xhs_upload_hint(self, page: Page) -> str:
        hint_keywords = [
            ("text=图片格式", "Detected image format warning on XHS."),
            ("text=请切换到图文", "Current page is likely video mode; switch to 图文 tab."),
            ("text=上传视频", "Current page appears to be video publish mode."),
            ("text=视频格式", "Detected video format validation message."),
        ]
        for selector, message in hint_keywords:
            with contextlib.suppress(Exception):
                locator = page.locator(selector)
                if await locator.count() > 0:
                    return message
        return ""

    async def _fill_text(self, page: Page, selectors: list[str], content: str) -> bool:
        value = content.strip()
        if not value:
            return False
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            with contextlib.suppress(Exception):
                await locator.first.fill(value, timeout=3_000)
                return True

        textboxes = page.get_by_role("textbox")
        total = await textboxes.count()
        for idx in range(total):
            with contextlib.suppress(Exception):
                await textboxes.nth(idx).fill(value, timeout=2_000)
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
