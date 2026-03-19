from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable

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
    image_only: bool = False,
) -> bool:
    files = [os.path.abspath(str(Path(path).expanduser())) for path in image_paths]

    for selector in input_selectors:
        if image_only and selector == "input[type='file']":
            continue
        locator = page.locator(selector)
        count = await locator.count()
        if count == 0:
            continue
        for idx in range(count):
            with contextlib.suppress(Exception):
                if image_only:
                    accept = (await locator.nth(idx).get_attribute("accept") or "").lower()
                    if "image" not in accept and not any(
                        ext in accept for ext in ("png", "jpg", "jpeg", "webp")
                    ):
                        continue
                await locator.nth(idx).set_input_files(files, timeout=4_000)
                return True

    for keyword in upload_keywords:
        target = page.get_by_text(keyword, exact=False)
        count = await target.count()
        if count == 0:
            continue
        for idx in range(count):
            with contextlib.suppress(Exception):
                if image_only:
                    candidate = target.nth(idx)
                    text_value = (await candidate.inner_text() or "").lower()
                    aria_label = (await candidate.get_attribute("aria-label") or "").lower()
                    merged_text = f"{keyword.lower()} {text_value} {aria_label}"
                    if not any(
                        token in merged_text
                        for token in ("\u56fe", "image", "photo", "picture", "png", "jpg")
                    ):
                        continue
                print(">>> 准备拦截文件选择器...")
                async with page.expect_file_chooser(timeout=4_000) as chooser_info:
                    await target.nth(idx).click(timeout=3_000)
                chooser = await chooser_info.value
                print(f">>> 成功拦截，准备注入图片路径: {files}")
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
        if str(getattr(adapter, "name", "")).lower() == "xhs":
            timeout_sec = max(timeout_sec, 660)

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
            step_timeout = self._step_timeout_seconds(self.settings.browser_operation_timeout_sec or 240)
            await self._await_with_timeout(
                page.goto(adapter.creator_center_url, wait_until="domcontentloaded"),
                timeout_sec=step_timeout,
                step_name="opening creator center",
            )
            await page.wait_for_timeout(1_200)

            if await self._need_login_robust_visible(page):
                total_timeout = int(self.settings.browser_operation_timeout_sec or 240)
                login_wait_timeout = min(max(total_timeout // 2, 45), 120)
                page_after_login = await self._wait_for_manual_login_robust(context, timeout_sec=login_wait_timeout)
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
                await self._await_with_timeout(
                    page.goto(adapter.creator_center_url, wait_until="domcontentloaded"),
                    timeout_sec=step_timeout,
                    step_name="re-opening creator center after login",
                )
                await page.wait_for_timeout(1_500)

            opened = await self._is_editor_ready(page)
            if not opened:
                opened = await self._await_with_timeout(
                    self._open_publish_entry(
                        page,
                        adapter.publish_entry_keywords,
                        adapter.fallback_publish_entry_selectors,
                    ),
                    timeout_sec=step_timeout,
                    step_name="opening publish entry",
                )
            if not opened:
                return BrowserOperationResult(
                    status="failed",
                    live_url="",
                    note="Publish entry not found. Open publish page manually then retry.",
                )

            await page.wait_for_timeout(2_000)
            await self._await_with_timeout(
                self._ensure_platform_publish_mode(page, adapter.name, image_paths),
                timeout_sec=step_timeout,
                step_name="switching to image publish mode",
            )
            if adapter.name.lower() == "xhs":
                title_filled, content_filled = await self._fill_xhs_title_and_content_resilient(
                    page, title, content
                )
            else:
                title_filled = await self._fill_text(page, adapter.title_selectors, title)
                content_filled = await self._fill_text(page, adapter.content_selectors, content)

            uploaded = await self._await_with_timeout(
                upload_files(
                    page=page,
                    image_paths=image_paths,
                    input_selectors=adapter.upload_input_selectors,
                    upload_keywords=adapter.upload_trigger_keywords,
                    image_only=adapter.name.lower() == "xhs",
                ),
                timeout_sec=step_timeout,
                step_name="uploading images",
            )
            if not uploaded and adapter.name.lower() == "xhs":
                with contextlib.suppress(Exception):
                    await self._ensure_xhs_image_tab_guarded(page, image_paths)
                    await page.wait_for_timeout(1_000)
                uploaded = await upload_files(
                    page=page,
                    image_paths=image_paths,
                    input_selectors=adapter.upload_input_selectors,
                    upload_keywords=adapter.upload_trigger_keywords,
                    image_only=True,
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

            if adapter.name.lower() == "xhs":
                await page.wait_for_timeout(2_500)
                retry_title_filled, retry_content_filled = await self._fill_xhs_title_and_content_resilient(
                    page, title, content
                )
                title_filled = title_filled or retry_title_filled
                content_filled = content_filled or retry_content_filled

            if adapter.name.lower() == "xhs" and (not title_filled or not content_filled):
                missing_parts = []
                if not title_filled:
                    missing_parts.append("title")
                if not content_filled:
                    missing_parts.append("content")
                keep_session_open = self._keep_session_for_manual_takeover(
                    keep_session_open=keep_session_open,
                    playwright=playwright,
                    context=context,
                    launched_browser=launched_browser,
                )
                return BrowserOperationResult(
                    status="ready",
                    live_url="",
                    note=(
                        "Browser is open for manual takeover. "
                        f"Auto-fill missed: {', '.join(missing_parts)}. "
                        "Please fill them manually and publish yourself."
                    ),
                )

            publish_button_found = await self._await_with_timeout(
                self._find_publish_button(
                    page,
                    adapter.publish_button_keywords,
                    adapter.publish_button_selectors,
                ),
                timeout_sec=step_timeout,
                step_name="checking publish button",
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
            keep_session_open = self._keep_session_for_manual_takeover(
                keep_session_open=keep_session_open,
                playwright=playwright,
                context=context,
                launched_browser=launched_browser,
            )
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

    def _keep_session_for_manual_takeover(
        self,
        keep_session_open: bool,
        playwright: Any | None,
        context: BrowserContext | None,
        launched_browser: Any | None,
    ) -> bool:
        if keep_session_open or not self.settings.browser_keep_alive:
            return keep_session_open
        if context is not None and playwright is not None:
            self._live_sessions.append((playwright, context, launched_browser))
            return True
        return keep_session_open

    def _summarize_exception(self, exc: Exception) -> str:
        text = " ".join(str(exc).splitlines())
        text = " ".join(text.split())
        if len(text) > 260:
            text = text[:260] + "..."
        return text

    def _step_timeout_seconds(self, total_timeout_sec: int) -> int:
        total = int(total_timeout_sec or 240)
        if total <= 0:
            total = 240
        return min(max(total // 3, 20), 60)

    async def _await_with_timeout(
        self,
        operation: Awaitable[Any],
        timeout_sec: int,
        step_name: str,
    ) -> Any:
        try:
            return await asyncio.wait_for(operation, timeout=max(timeout_sec, 1))
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"Timed out while {step_name} after {timeout_sec}s") from exc

    async def _has_visible_text(self, page: Page, text: str) -> bool:
        with contextlib.suppress(Exception):
            locator = page.get_by_text(text, exact=False)
            count = await locator.count()
            for idx in range(min(count, 3)):
                with contextlib.suppress(Exception):
                    if await locator.nth(idx).is_visible():
                        return True
        return False

    async def _has_visible_locator(self, page: Page, selector: str) -> bool:
        with contextlib.suppress(Exception):
            locator = page.locator(selector)
            count = await locator.count()
            for idx in range(min(count, 3)):
                with contextlib.suppress(Exception):
                    if await locator.nth(idx).is_visible():
                        return True
        return False

    async def _is_editor_ready(self, page: Page) -> bool:
        editor_selectors = (
            "input[accept*='image']",
            "textarea",
            "[contenteditable='true']",
            "button:has-text('\\u53d1\\u5e03')",
            "[role='button']:has-text('\\u53d1\\u5e03')",
        )
        for selector in editor_selectors:
            if await self._has_visible_locator(page, selector):
                return True
        return False

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

    async def _need_login_visible(self, page: Page) -> bool:
        login_keywords = (
            "\u767b\u5f55",
            "\u626b\u7801\u767b\u5f55",
            "\u624b\u673a\u53f7\u767b\u5f55",
            "Sign in",
            "Log in",
        )
        for keyword in login_keywords:
            if await self._has_visible_text(page, keyword):
                return True
        return await self._has_visible_locator(page, "input[type='password']")

    async def _need_login_robust_visible(self, page: Page) -> bool:
        if await self._is_editor_ready(page):
            return False

        with contextlib.suppress(Exception):
            current_url = (page.url or "").lower()
            if any(token in current_url for token in ("login", "signin", "passport")):
                return True

        return await self._need_login_visible(page)

    async def _wait_for_manual_login_robust(self, context: BrowserContext, timeout_sec: int) -> Page | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout_sec, 1)
        while loop.time() < deadline:
            for page in list(context.pages):
                if page.is_closed():
                    continue
                page_url = (page.url or "").strip().lower()
                if page_url.startswith("about:blank"):
                    continue
                if not await self._need_login_robust_visible(page):
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

    async def _ensure_platform_publish_mode(
        self,
        page: Page,
        platform_name: str,
        image_paths: list[str],
    ) -> None:
        if platform_name.lower() == "xhs":
            await self._ensure_xhs_image_tab_guarded(page, image_paths)

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

    async def _fill_xhs_title_and_content_fast(self, page: Page, title: str, content: str) -> tuple[bool, bool]:
        title_value = title.strip()
        content_value = content.strip()
        title_filled = False
        content_filled = False

        if title_value:
            title_selectors = [
                "input[placeholder*='填写标题']",
                "input.c-input_inner",
                "input[placeholder*='标题']",
            ]
            for selector in title_selectors:
                with contextlib.suppress(Exception):
                    locator = page.locator(selector)
                    if await locator.count() == 0:
                        continue
                    target = locator.first
                    if not await target.is_visible():
                        continue
                    await target.click(timeout=10_000)
                    await target.fill(title_value, timeout=10_000)
                    title_filled = True
                    break

        if content_value:
            content_selectors = [
                "div.ql-editor",
                "div[contenteditable='true'][placeholder*='输入正文']",
                "[placeholder*='输入正文']",
                "div[contenteditable='true']",
            ]
            for selector in content_selectors:
                with contextlib.suppress(Exception):
                    locator = page.locator(selector)
                    if await locator.count() == 0:
                        continue
                    target = locator.first
                    if not await target.is_visible():
                        continue
                    await target.click(timeout=10_000)
                    with contextlib.suppress(Exception):
                        await page.keyboard.press("Control+A")
                        await page.keyboard.press("Backspace")
                    await page.keyboard.insert_text(content_value)
                    content_filled = True
                    break

        return title_filled, content_filled

    async def _fill_xhs_title_and_content_resilient(
        self, page: Page, title: str, content: str
    ) -> tuple[bool, bool]:
        title_value = title.strip()
        content_value = content.strip()
        title_filled = False
        content_filled = False

        if title_value:
            title_filled = await self._fill_xhs_title(page, title_value)

        if content_value:
            content_filled = await self._fill_xhs_content(page, content_value)

        return title_filled, content_filled

    async def _fill_xhs_title(self, page: Page, title_value: str) -> bool:
        title_selectors = [
            "input[placeholder*='\u586b\u5199\u6807\u9898']",
            "input[placeholder*='\u6dfb\u52a0\u6807\u9898']",
            "input[placeholder*='\u6807\u9898']",
            "textarea[placeholder*='\u6807\u9898']",
            "[contenteditable='true'][data-placeholder*='\u6807\u9898']",
            "[role='textbox'][data-placeholder*='\u6807\u9898']",
            "input.c-input_inner",
            "input[type='text']",
        ]
        for selector in title_selectors:
            with contextlib.suppress(Exception):
                locator = page.locator(selector)
                count = await locator.count()
                for idx in range(count):
                    target = locator.nth(idx)
                    if not await target.is_visible():
                        continue
                    if await self._fill_text_target(page, target, title_value):
                        return True
        return False

    async def _fill_xhs_content(self, page: Page, content_value: str) -> bool:
        content_selectors = [
            "div.ql-editor",
            "div[contenteditable='true'][data-placeholder*='\u8f93\u5165\u6b63\u6587']",
            "div[contenteditable='true'][data-placeholder*='\u8f93\u5165\u6b63\u6587\u63cf\u8ff0']",
            "div[contenteditable='true'][data-placeholder*='\u6b63\u6587']",
            "textarea[placeholder*='\u8f93\u5165\u6b63\u6587']",
            "textarea[placeholder*='\u8f93\u5165\u6b63\u6587\u63cf\u8ff0']",
            "textarea[placeholder*='\u6b63\u6587']",
            "textarea[placeholder*='\u5185\u5bb9']",
            "[role='textbox'][data-placeholder*='\u6b63\u6587']",
            "div[contenteditable='true']",
        ]
        for selector in content_selectors:
            with contextlib.suppress(Exception):
                locator = page.locator(selector)
                count = await locator.count()
                for idx in range(count):
                    target = locator.nth(idx)
                    if not await target.is_visible():
                        continue
                    if await self._fill_text_target(page, target, content_value):
                        return True
        return False

    async def _fill_text_target(self, page: Page, target: Any, value: str) -> bool:
        with contextlib.suppress(Exception):
            await target.scroll_into_view_if_needed(timeout=5_000)
        with contextlib.suppress(Exception):
            await target.click(timeout=10_000)

        with contextlib.suppress(Exception):
            await target.fill(value, timeout=10_000)
            if await self._target_contains_value(target, value):
                return True

        with contextlib.suppress(Exception):
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        with contextlib.suppress(Exception):
            await page.keyboard.insert_text(value)
            if await self._target_contains_value(target, value):
                return True

        with contextlib.suppress(Exception):
            await target.evaluate(
                """(el, text) => {
                    if ('value' in el) {
                        el.value = text;
                    } else {
                        el.innerHTML = '';
                        el.textContent = text;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }""",
                value,
            )
            if await self._target_contains_value(target, value):
                return True

        return False

    async def _target_contains_value(self, target: Any, value: str) -> bool:
        current_text = await target.evaluate(
            """(el) => {
                if ('value' in el) return el.value || '';
                return el.innerText || el.textContent || '';
            }"""
        )
        current_value = " ".join(str(current_text).split())
        expected = " ".join(value.split())
        if not expected:
            return False
        return expected[:10] in current_value or expected[:20] in current_value

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

    async def _looks_like_xhs_image_mode_strict(self, page: Page) -> bool:
        image_selectors = (
            "input[type='file'][accept*='image']",
            "input[type='file'][accept*='png']",
            "input[type='file'][accept*='jpg']",
            "input[type='file'][accept*='jpeg']",
            "text=\u6dfb\u52a0\u56fe\u7247",
            "text=\u9009\u62e9\u56fe\u7247",
            "text=\u4e0a\u4f20\u56fe\u7247",
        )
        for selector in image_selectors:
            if await self._has_visible_locator(page, selector):
                return True
        return False

    async def _ensure_xhs_image_tab_strict(self, page: Page) -> bool:
        if await self._looks_like_xhs_image_mode_strict(page):
            return True

        print(">>> 正在切换到【上传图文】模式...")
        with contextlib.suppress(Exception):
            await page.get_by_text("\u4e0a\u4f20\u56fe\u6587", exact=True).click(timeout=15_000)
            await page.wait_for_timeout(2_000)
            if await self._looks_like_xhs_image_mode_strict(page):
                return True

        selectors = [
            "button:has-text('\u4e0a\u4f20\u56fe\u6587')",
            "[role='tab']:has-text('\u4e0a\u4f20\u56fe\u6587')",
            "div:has-text('\u4e0a\u4f20\u56fe\u6587')",
            "span:has-text('\u4e0a\u4f20\u56fe\u6587')",
            "a:has-text('\u4e0a\u4f20\u56fe\u6587')",
            "button:has-text('\u56fe\u6587')",
            "[role='tab']:has-text('\u56fe\u6587')",
            "div:has-text('\u56fe\u6587')",
            "span:has-text('\u56fe\u6587')",
            "a:has-text('\u56fe\u6587')",
            "text=\u4e0a\u4f20\u56fe\u6587",
            "text=\u56fe\u6587",
        ]
        for _ in range(3):
            for selector in selectors:
                with contextlib.suppress(Exception):
                    locator = page.locator(selector)
                    if await locator.count() == 0:
                        continue
                    await locator.first.click(timeout=8_000)
                    await page.wait_for_timeout(1_200)
                    if await self._looks_like_xhs_image_mode_strict(page):
                        return True
        return await self._looks_like_xhs_image_mode_strict(page)

    async def _click_with_file_chooser_guard(
        self,
        page: Page,
        locator: Any,
        files: list[str],
        click_timeout_ms: int = 5_000,
        chooser_timeout_ms: int = 2_500,
    ) -> bool:
        try:
            print(">>> 准备拦截文件选择器...")
            async with page.expect_file_chooser(timeout=chooser_timeout_ms) as chooser_info:
                await locator.click(timeout=click_timeout_ms)
            file_chooser = await chooser_info.value
            print(f">>> 成功拦截，准备注入图片路径: {files}")
            await file_chooser.set_files(files)
            return True
        except PlaywrightTimeoutError:
            return False

    async def _ensure_xhs_image_tab_guarded(self, page: Page, image_paths: list[str]) -> bool:
        if await self._looks_like_xhs_image_mode_strict(page):
            return True

        files = [os.path.abspath(str(Path(path).expanduser())) for path in image_paths]
        print(">>> 正在切换到【上传图文】模式...")

        with contextlib.suppress(Exception):
            upload_tab = page.get_by_text("\u4e0a\u4f20\u56fe\u6587", exact=True)
            intercepted = await self._click_with_file_chooser_guard(
                page=page,
                locator=upload_tab,
                files=files,
                click_timeout_ms=15_000,
                chooser_timeout_ms=3_000,
            )
            if not intercepted:
                await upload_tab.click(timeout=15_000)
            await page.wait_for_timeout(1_600)
            if await self._looks_like_xhs_image_mode_strict(page):
                return True

        tab_selectors = [
            "button[role='tab']:has-text('\u56fe\u6587')",
            "[role='tab']:has-text('\u56fe\u6587')",
            "button:has-text('\u56fe\u6587')",
            "a:has-text('\u56fe\u6587')",
        ]
        for _ in range(2):
            for selector in tab_selectors:
                with contextlib.suppress(Exception):
                    locator = page.locator(selector)
                    if await locator.count() == 0:
                        continue
                    first = locator.first
                    if not await first.is_visible():
                        continue
                    await first.click(timeout=4_000)
                    await page.wait_for_timeout(800)
                    if await self._looks_like_xhs_image_mode_strict(page):
                        return True
        return await self._looks_like_xhs_image_mode_strict(page)

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
