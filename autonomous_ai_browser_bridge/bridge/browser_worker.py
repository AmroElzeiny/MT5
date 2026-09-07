from __future__ import annotations

import asyncio
import json
import hashlib
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .audit import log_event
from .browser import browser_state, discover_browser
from .config import settings
from .job_store import job_store
from .prompting import (
    delivery_instruction,
    filename_matches_job,
    response_filename,
    response_filename_token,
)
from .registry import profile_by_alias
from .response_extract import extract_json_object_text, read_response_file

_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class BrowserProviderRateLimit(RuntimeError):
    """An explicit provider UI rate-limit, safe to route to a configured fallback."""

    def __init__(self, provider: str, marker: str, *, after_send: bool) -> None:
        self.provider = str(provider)
        self.marker = str(marker)
        self.after_send = bool(after_send)
        super().__init__(
            f"{self.provider}_rate_limit:{self.marker}:after_send={str(self.after_send).lower()}"
        )


def _safe_name(value: str) -> str:
    value = _SAFE.sub("_", value or "request").strip("._")
    return value[:100] or "request"


def _matching_rate_limit_marker(text: str, patterns: tuple[str, ...]) -> str:
    haystack = str(text or "").casefold()
    for pattern in patterns:
        marker = str(pattern or "").strip()
        if marker and marker.casefold() in haystack:
            return marker
    return ""


def _selector_value(template: str, value: str) -> str:
    return template.replace("{value}", value.replace('"', '\\"'))


def _schema_identity(schema: dict[str, Any] | None) -> tuple[str, str, list[str]]:
    if not isinstance(schema, dict):
        return "structured_response", "none", []
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    name = str(schema.get("title") or schema.get("$id") or "structured_response")
    required = [str(key) for key in schema.get("required", []) if isinstance(key, str)]
    return name, fingerprint, required


def _schema_errors(schema: dict[str, Any], obj: dict[str, Any]) -> list[Any]:
    return sorted(
        Draft202012Validator(schema).iter_errors(obj),
        key=lambda error: list(error.absolute_path),
    )


def _schema_error_details(errors: list[Any]) -> list[str]:
    details: list[str] = []
    for error in errors:
        path = "/".join(str(part) for part in error.absolute_path) or "<root>"
        if error.validator == "maxLength":
            message = (
                f"string length {len(str(error.instance))} exceeds maxLength "
                f"{int(error.validator_value)}; shorten it without changing the judgment"
            )
        elif error.validator == "minLength":
            message = (
                f"string length {len(str(error.instance))} is below minLength "
                f"{int(error.validator_value)}"
            )
        elif error.validator in {"maxItems", "minItems"}:
            message = (
                f"list length {len(error.instance) if isinstance(error.instance, list) else 'invalid'} "
                f"violates {error.validator}={error.validator_value}"
            )
        else:
            message = error.message[:240]
        details.append(f"{path}: {message}")
    return details


def _schema_turn_binding(job: dict[str, Any], schema: dict[str, Any] | None) -> str:
    name, fingerprint, required = _schema_identity(schema)
    required_json = json.dumps(required, ensure_ascii=False, separators=(",", ":"))
    return "\n".join([
        f"CURRENT BRIDGE REQUEST ID: {job.get('request_id') or 'unknown'}",
        f"CURRENT RESPONSE SCHEMA: {name}",
        f"CURRENT SCHEMA FINGERPRINT: {fingerprint}",
        f"CURRENT REQUIRED TOP-LEVEL KEYS: {required_json}",
        "Ignore every earlier attachment and response shape in this conversation.",
        "Only the schema and evidence attached to THIS message are authoritative.",
    ])


def _schema_repair_prompt(
    job: dict[str, Any],
    schema: dict[str, Any],
    errors: list[Any],
    *,
    response_mode: str | None = None,
) -> str:
    details = _schema_error_details(errors)
    expected = response_filename(job["id"])
    return "\n\n".join([
        "SCHEMA CORRECTION FOR THE CURRENT REQUEST. Your previous object was rejected and has no authority.",
        _schema_turn_binding(job, schema),
        "Validation failures:\n- " + "\n- ".join(details[:12]),
        (
            "Return a fresh answer for the SAME evidence and substantive judgment, but make it satisfy the "
            "current schema exactly. Do not copy a Critic or Adjudicator shape into an Analyst response, or vice versa."
        ),
        delivery_instruction(mode=response_mode, filename=expected),
        f"The attached output file must be named exactly: {expected}",
    ])


def _configured_chat_tab_urls(tab_count: int, start_url: str) -> list[str]:
    """Resolve exactly one initial conversation URL per managed browser tab."""
    explicit = list(settings.browser_chat_tab_urls)
    if explicit:
        if len(explicit) != tab_count:
            raise RuntimeError(
                "BROWSER_CHAT_TAB_URLS_count_mismatch:"
                f"expected={tab_count}:got={len(explicit)}"
            )
        if len(set(explicit)) != len(explicit):
            raise RuntimeError("BROWSER_CHAT_TAB_URLS_must_be_unique")
        return explicit
    if settings.browser_chat_new_url:
        return [settings.browser_chat_new_url] * tab_count
    if tab_count == 1:
        return [start_url]
    raise RuntimeError(
        "multi_chat_requires_BROWSER_CHAT_NEW_URL_or_BROWSER_CHAT_TAB_URLS"
    )


@dataclass
class _ChatTabSlot:
    page: Any
    number: int
    bound_alias: str
    submitted_jobs: int = 0


@dataclass
class _AsyncChatTabLane:
    page: Any
    number: int
    request_id: str = ""
    release_after: float = 0.0
    submitted_jobs: int = 0
    submissions_since_refresh: int = 0
    current_job_id: str = ""
    fallback_active: bool = False


class _ChatTabRotation:
    """Sequential batches across persistent tabs; never parallelizes jobs."""

    def __init__(self, slots: list[_ChatTabSlot], requests_per_tab: int) -> None:
        if not slots:
            raise ValueError("chat_tab_rotation_requires_at_least_one_tab")
        if requests_per_tab < 1:
            raise ValueError("requests_per_tab_must_be_positive")
        self.slots = slots
        self.requests_per_tab = requests_per_tab
        self.active_index = 0
        self.requests_in_turn = 0
        self.total_submissions = 0

    @property
    def current(self) -> _ChatTabSlot:
        return self.slots[self.active_index]

    def record_submission(self) -> dict[str, Any]:
        submitted_slot = self.current
        submitted_tab = submitted_slot.number
        submitted_slot.submitted_jobs += 1
        self.requests_in_turn += 1
        self.total_submissions += 1
        submitted_tab_count = self.requests_in_turn
        rotated = self.requests_in_turn >= self.requests_per_tab
        if rotated:
            self.active_index = (self.active_index + 1) % len(self.slots)
            self.requests_in_turn = 0
        return {
            "submitted_tab": submitted_tab,
            "submitted_tab_count": submitted_tab_count,
            "rotated": rotated,
            "next_tab": self.current.number,
        }

    def duplicate_submitted_urls(self) -> dict[str, list[int]]:
        by_url: dict[str, list[int]] = {}
        for slot in self.slots:
            if slot.submitted_jobs < 1:
                continue
            url = str(slot.page.url or "").strip()
            if not url or url == "about:blank":
                continue
            by_url.setdefault(url, []).append(slot.number)
        return {url: tabs for url, tabs in by_url.items() if len(tabs) > 1}

    def state(self) -> dict[str, int]:
        return {
            "chat_tab_count": len(self.slots),
            "open_chat_tabs": len(self.slots),
            "active_chat_tab": self.current.number,
            "requests_on_active_tab": self.requests_in_turn,
            "requests_per_tab": self.requests_per_tab,
            "total_tab_submissions": self.total_submissions,
        }


class AutonomousBrowserWorker:
    """Independent per-tab browser lanes sharing one Playwright context.

    Every lane owns at most one PO3 request at a time. Analyst, Critic and
    Adjudicator jobs carrying the same request id stay on that lane and in the
    same fresh conversation. A different request is never sent on the tab until
    the previous response has been received (or its absolute deadline expires).

    Safety/reliability invariant: the original evidence request is sent once.
    A schema-invalid JSON object has no authority and may receive at most one
    correction turn bound to the same request, schema, deadline and evidence.
    Only a strictly validated object is returned to PO3.
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if not settings.browser_automation_enabled:
            browser_state.update(ready=False, running=False, error="automation_disabled", last_event="disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="autonomous-browser-worker")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)

    def _run(self) -> None:
        if not settings.browser_configured:
            error = "selectors_not_configured:BROWSER_CHAT_INPUT_SELECTOR,BROWSER_ASSISTANT_MESSAGE_SELECTOR"
            browser_state.update(ready=False, running=False, error=error, last_event="configuration_block")
            log_event("browser_worker_configuration_block", error=error)
            return
        try:
            asyncio.run(self._run_parallel())
        except Exception as exc:
            err = f"{type(exc).__name__}:{exc}"
            browser_state.update(ready=False, running=False, error=err, last_event="browser_crashed")
            log_event("browser_worker_crashed", error=err)
        finally:
            browser_state.update(
                running=False,
                active_request_id="",
                active_request_ids={},
                busy_chat_tabs=0,
            )

    async def _run_parallel(self) -> None:
        from playwright.async_api import async_playwright

        start_url = settings.browser_start_url or settings.browser_chat_new_url or "about:blank"
        tab_urls = _configured_chat_tab_urls(settings.browser_chat_tab_count, start_url)
        if not settings.browser_chat_new_url:
            raise RuntimeError("fresh_conversation_requires_BROWSER_CHAT_NEW_URL")
        name, executable = discover_browser()
        settings.browser_profile_dir.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=str(settings.browser_profile_dir.resolve()),
                executable_path=str(executable),
                headless=settings.browser_headless,
                accept_downloads=True,
                viewport=None if not settings.browser_headless else {"width": 1440, "height": 1000},
                args=["--no-first-run"],
            )
            context.set_default_timeout(settings.selector_timeout_ms)
            context.set_default_navigation_timeout(settings.navigation_timeout_ms)
            gemini_context: Any | None = None
            gemini_lanes: list[_AsyncChatTabLane] = []
            try:
                lanes = await self._a_open_chat_tabs(context, tab_urls)
                if settings.gemini_fallback_enabled:
                    if not settings.gemini_configured:
                        log_event(
                            "gemini_fallback_unavailable",
                            reason="selectors_or_new_chat_url_not_configured",
                        )
                    else:
                        try:
                            settings.gemini_profile_dir.mkdir(parents=True, exist_ok=True)
                            gemini_context = await pw.chromium.launch_persistent_context(
                                user_data_dir=str(settings.gemini_profile_dir.resolve()),
                                executable_path=str(executable),
                                headless=settings.browser_headless,
                                accept_downloads=True,
                                viewport=(
                                    None
                                    if not settings.browser_headless
                                    else {"width": 1440, "height": 1000}
                                ),
                                args=["--no-first-run"],
                            )
                            gemini_context.set_default_timeout(settings.selector_timeout_ms)
                            gemini_context.set_default_navigation_timeout(
                                settings.navigation_timeout_ms
                            )
                            gemini_lanes = await self._a_open_gemini_tabs(
                                gemini_context, settings.browser_chat_tab_count
                            )
                        except Exception as exc:
                            log_event(
                                "gemini_fallback_unavailable",
                                reason=f"{type(exc).__name__}:{exc}",
                            )
                            if gemini_context is not None:
                                try:
                                    await gemini_context.close()
                                except Exception:
                                    pass
                            gemini_context = None
                            gemini_lanes = []
                browser_state.update(
                    ready=True,
                    running=True,
                    error="",
                    browser=name,
                    executable=str(executable),
                    profile_dir=str(settings.browser_profile_dir),
                    automation=True,
                    headless=settings.browser_headless,
                    configured=True,
                    current_url=lanes[0].page.url,
                    last_event="browser_started",
                    chat_tab_count=len(lanes),
                    open_chat_tabs=len(lanes),
                    active_chat_tab=0,
                    busy_chat_tabs=0,
                    active_request_ids={},
                    requests_on_active_tab=0,
                    requests_per_tab=settings.browser_requests_per_tab,
                    total_tab_submissions=0,
                    gemini_fallback_enabled=settings.gemini_fallback_enabled,
                    gemini_fallback_ready=len(gemini_lanes) == len(lanes),
                    gemini_open_tabs=len(gemini_lanes),
                    gemini_profile_dir=str(settings.gemini_profile_dir),
                )
                log_event(
                    "browser_worker_started",
                    browser=name,
                    executable=str(executable),
                    headless=settings.browser_headless,
                    chat_tab_count=len(lanes),
                    lane_mode="parallel_request_affinity",
                    temporary_chat_url=settings.browser_chat_new_url,
                    refresh_after_submissions=settings.browser_requests_per_tab,
                    max_inflight_per_tab=1,
                    gemini_fallback_ready=len(gemini_lanes) == len(lanes),
                )
                active_roots: dict[str, int] = {}
                totals = {"submissions": 0}
                tasks = [
                    asyncio.create_task(
                        self._lane_loop(
                            lane,
                            active_roots,
                            totals,
                            gemini_lanes[lane.number - 1] if gemini_lanes else None,
                        ),
                        name=f"browser-tab-lane-{lane.number}",
                    )
                    for lane in lanes
                ]
                await asyncio.gather(*tasks)
            finally:
                if gemini_context is not None:
                    await gemini_context.close()
                await context.close()

    async def _a_open_chat_tabs(self, context: Any, urls: list[str]) -> list[_AsyncChatTabLane]:
        pages = list(context.pages)
        while len(pages) < len(urls):
            pages.append(await context.new_page())
        for extra in pages[len(urls):]:
            try:
                await extra.close()
            except Exception:
                pass
        lanes: list[_AsyncChatTabLane] = []
        for index, (page, url) in enumerate(zip(pages[:len(urls)], urls), start=1):
            if url and url != "about:blank" and page.url != url:
                await page.goto(url, wait_until="domcontentloaded", timeout=settings.navigation_timeout_ms)
            if settings.login_ready_selector:
                await page.locator(settings.login_ready_selector).first.wait_for(
                    state="visible", timeout=settings.selector_timeout_ms
                )
            lanes.append(_AsyncChatTabLane(page=page, number=index))
            log_event(
                "browser_chat_tab_opened",
                tab=index,
                initial_url=url,
                current_url=page.url,
                worker_mode="independent_lane",
            )
        await lanes[0].page.bring_to_front()
        return lanes

    async def _a_open_gemini_tabs(
        self, context: Any, tab_count: int
    ) -> list[_AsyncChatTabLane]:
        pages = list(context.pages)
        while len(pages) < tab_count:
            pages.append(await context.new_page())
        for extra in pages[tab_count:]:
            try:
                await extra.close()
            except Exception:
                pass
        lanes: list[_AsyncChatTabLane] = []
        for index, page in enumerate(pages[:tab_count], start=1):
            if page.url != settings.gemini_chat_new_url:
                await page.goto(
                    settings.gemini_chat_new_url,
                    wait_until="domcontentloaded",
                    timeout=settings.navigation_timeout_ms,
                )
            if settings.gemini_login_ready_selector:
                await page.locator(settings.gemini_login_ready_selector).first.wait_for(
                    state="visible", timeout=settings.selector_timeout_ms
                )
            await self._a_apply_gemini_preferences(page)
            lanes.append(_AsyncChatTabLane(page=page, number=index))
            log_event(
                "gemini_fallback_tab_opened",
                tab=index,
                current_url=page.url,
                profile_dir=str(settings.gemini_profile_dir),
            )
        return lanes

    def _parallel_state(
        self,
        lanes: list[_AsyncChatTabLane] | None,
        active_roots: dict[str, int],
        totals: dict[str, int],
        *,
        active_tab: int = 0,
        current_url: str = "",
        event: str = "",
        error: str | None = None,
    ) -> None:
        fields: dict[str, Any] = {
            "active_request_ids": {
                str(tab): request_id for request_id, tab in sorted(active_roots.items(), key=lambda item: item[1])
            },
            "active_request_id": next(iter(active_roots), ""),
            "busy_chat_tabs": len(active_roots),
            "active_chat_tab": active_tab,
            "total_tab_submissions": int(totals.get("submissions", 0)),
        }
        if lanes is not None:
            fields["open_chat_tabs"] = len(lanes)
            fields["chat_tab_count"] = len(lanes)
        if current_url:
            fields["current_url"] = current_url
        if event:
            fields["last_event"] = event
        if error is not None:
            fields["error"] = error
        browser_state.update(**fields)

    @staticmethod
    def _job_schema_name(job: dict[str, Any]) -> str:
        try:
            schema = json.loads(job.get("schema_json") or "{}")
        except Exception:
            return ""
        return str(schema.get("title") or schema.get("$id") or "")

    async def _lane_loop(
        self,
        lane: _AsyncChatTabLane,
        active_roots: dict[str, int],
        totals: dict[str, int],
        gemini_lane: _AsyncChatTabLane | None = None,
    ) -> None:
        while not self._stop.is_set():
            if lane.number == 1:
                await self._a_drain_commands(lane)

            job: dict[str, Any] | None = None
            if lane.request_id:
                job = job_store.claim_next_pending(preferred_request_id=lane.request_id)
                if job is None:
                    if time.monotonic() < lane.release_after:
                        await asyncio.sleep(0.1)
                        continue
                    if (
                        lane.submissions_since_refresh
                        >= settings.browser_requests_per_tab
                    ):
                        try:
                            await lane.page.goto(
                                settings.browser_chat_new_url,
                                wait_until="domcontentloaded",
                                timeout=settings.navigation_timeout_ms,
                            )
                            if settings.login_ready_selector:
                                await lane.page.locator(
                                    settings.login_ready_selector
                                ).first.wait_for(
                                    state="visible",
                                    timeout=settings.selector_timeout_ms,
                                )
                            log_event(
                                "browser_temporary_chat_refreshed",
                                request_id=lane.request_id,
                                tab=lane.number,
                                submissions=lane.submissions_since_refresh,
                                url=lane.page.url,
                            )
                            lane.submissions_since_refresh = 0
                        except Exception as exc:
                            log_event(
                                "browser_temporary_chat_refresh_failed",
                                request_id=lane.request_id,
                                tab=lane.number,
                                error=f"{type(exc).__name__}:{exc}",
                            )
                    active_roots.pop(lane.request_id, None)
                    log_event(
                        "browser_request_affinity_released",
                        request_id=lane.request_id,
                        tab=lane.number,
                        reason="role_sequence_complete_or_grace_elapsed",
                    )
                    lane.request_id = ""
                    lane.release_after = 0.0
                    lane.fallback_active = False
                    self._parallel_state(None, active_roots, totals, event="lane_idle")
                    continue
            else:
                job = job_store.claim_next_pending(excluded_request_ids=set(active_roots))
                if job is None:
                    await asyncio.sleep(0.15)
                    continue
                lane.request_id = str(job["request_id"])
                active_roots[lane.request_id] = lane.number
                try:
                    await self._a_start_fresh_conversation(lane, job)
                except Exception as exc:
                    error = f"{type(exc).__name__}:{exc}"
                    job_store.fail(job["id"], error)
                    active_roots.pop(lane.request_id, None)
                    lane.request_id = ""
                    self._parallel_state(
                        None, active_roots, totals, active_tab=lane.number,
                        event="fresh_conversation_failed", error=error,
                    )
                    continue

            lane.current_job_id = str(job["id"])
            schema_name = self._job_schema_name(job)
            succeeded = await self._a_process_job(
                lane, job, active_roots, totals, gemini_lane=gemini_lane
            )
            lane.current_job_id = ""
            if succeeded:
                lane.submitted_jobs += 1
                lane.submissions_since_refresh += 1
                totals["submissions"] = int(totals.get("submissions", 0)) + 1
                if schema_name == "ModelAdjudicatorDecision":
                    lane.release_after = time.monotonic()
                else:
                    lane.release_after = time.monotonic() + settings.browser_request_affinity_grace_ms / 1000.0
                log_event(
                    "browser_chat_tab_submission",
                    request_id=lane.request_id,
                    tab=lane.number,
                    schema=schema_name,
                    request_tab_submission=lane.submitted_jobs,
                    total_submissions=totals["submissions"],
                    inflight_on_tab=0,
                )
            else:
                active_roots.pop(lane.request_id, None)
                lane.request_id = ""
                lane.release_after = 0.0
                lane.fallback_active = False
            self._parallel_state(
                None,
                active_roots,
                totals,
                active_tab=lane.number,
                current_url=lane.page.url,
                event="job_completed" if succeeded else "job_failed",
                error="" if succeeded else None,
            )

    async def _a_drain_commands(self, lane: _AsyncChatTabLane) -> None:
        while True:
            try:
                command, value = browser_state.commands.get_nowait()
            except Exception:
                return
            if command == "open" and value:
                if lane.request_id:
                    log_event(
                        "browser_manual_navigation_deferred",
                        tab=lane.number,
                        request_id=lane.request_id,
                        url=value,
                    )
                    continue
                await lane.page.goto(value, wait_until="domcontentloaded")
                browser_state.update(current_url=lane.page.url, last_event="manual_navigation")

    async def _a_start_fresh_conversation(self, lane: _AsyncChatTabLane, job: dict[str, Any]) -> None:
        page = lane.page
        await page.bring_to_front()
        refreshed = (
            not page.url
            or page.url == "about:blank"
            or lane.submissions_since_refresh >= settings.browser_requests_per_tab
        )
        if refreshed:
            await page.goto(
                settings.browser_chat_new_url,
                wait_until="domcontentloaded",
                timeout=settings.navigation_timeout_ms,
            )
            lane.submissions_since_refresh = 0
        if settings.login_ready_selector:
            await page.locator(settings.login_ready_selector).first.wait_for(
                state="visible", timeout=settings.selector_timeout_ms
            )
        log_event(
            "browser_fresh_conversation_started",
            request_id=job["request_id"],
            job_id=job["id"],
            tab=lane.number,
            url=page.url,
            refreshed=refreshed,
            refresh_after_submissions=settings.browser_requests_per_tab,
        )

    async def _a_apply_profile_preferences(self, page: Any, profile: Any) -> None:
        if settings.model_control_selector and settings.model_option_selector_template and profile.desired_model:
            await page.locator(settings.model_control_selector).last.click()
            await page.locator(_selector_value(settings.model_option_selector_template, profile.desired_model)).last.click()
        if settings.effort_control_selector and settings.effort_option_selector_template and profile.desired_effort:
            await page.locator(settings.effort_control_selector).last.click()
            await page.locator(_selector_value(settings.effort_option_selector_template, profile.desired_effort)).last.click()

    async def _a_primary_rate_limit_marker(self, page: Any) -> str:
        try:
            body = await page.locator("body").inner_text(timeout=1000)
        except Exception:
            return ""
        return _matching_rate_limit_marker(
            body, settings.browser_rate_limit_text_patterns
        )

    async def _a_start_gemini_conversation(
        self, lane: _AsyncChatTabLane, job: dict[str, Any]
    ) -> None:
        page = lane.page
        await page.bring_to_front()
        await page.goto(
            settings.gemini_chat_new_url,
            wait_until="domcontentloaded",
            timeout=settings.navigation_timeout_ms,
        )
        if settings.gemini_login_ready_selector:
            await page.locator(settings.gemini_login_ready_selector).first.wait_for(
                state="visible", timeout=settings.selector_timeout_ms
            )
        await self._a_apply_gemini_preferences(page)
        log_event(
            "gemini_fallback_conversation_started",
            request_id=job["request_id"],
            job_id=job["id"],
            tab=lane.number,
            url=page.url,
        )

    async def _a_apply_gemini_preferences(self, page: Any) -> None:
        desired = settings.gemini_desired_model.strip()
        control_selector = settings.gemini_model_control_selector
        option_template = settings.gemini_model_option_selector_template
        if not desired or not control_selector or not option_template:
            return
        control = page.locator(control_selector).last
        await control.wait_for(state="visible", timeout=settings.selector_timeout_ms)
        before = (await control.inner_text()).strip()
        short_name = desired.split()[-1].casefold()
        if desired.casefold() not in before.casefold() and short_name not in before.casefold():
            await control.click(timeout=settings.selector_timeout_ms)
            option_selector = _selector_value(option_template, desired)
            option = page.locator(option_selector).last
            await option.wait_for(state="visible", timeout=settings.selector_timeout_ms)
            await option.click(timeout=settings.selector_timeout_ms)
            await asyncio.sleep(0.5)
        after = (await control.inner_text()).strip()
        if desired.casefold() not in after.casefold() and short_name not in after.casefold():
            raise RuntimeError(
                f"gemini_model_selection_failed:desired={desired}:observed={after}"
            )
        log_event(
            "gemini_fallback_model_selected",
            desired_model=desired,
            observed_model=after,
            changed=before != after,
        )

    async def _a_gemini_composer_set(self, page: Any, value: str) -> Any:
        composer = page.locator(settings.gemini_chat_input_selector).last
        await composer.wait_for(state="visible", timeout=settings.selector_timeout_ms)
        try:
            await composer.fill(value)
        except Exception:
            await composer.click()
            await composer.press("Control+A")
            await composer.press("Backspace")
            await composer.press_sequentially(value, delay=0)
        return composer

    def _prepare_gemini_upload(self, job: dict[str, Any]) -> Path:
        schema = json.loads(job["schema_json"]) if job.get("schema_json") else None
        expected = response_filename(job["id"])
        payload = {
            "bridge_request_id": job["request_id"],
            "instructions": job["prompt"],
            "response_schema": schema,
            "required_response_filename": expected,
            "response_requirement": delivery_instruction(
                mode=settings.gemini_response_mode,
                filename=expected,
            ),
        }
        filename = f"{_safe_name(job['request_id'])}-{job['id'][:8]}-gemini.json"
        path = settings.upload_dir / filename
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    async def _a_gemini_deliver(self, page: Any, job: dict[str, Any]) -> Any:
        prompt = str(job["prompt"])
        schema = json.loads(job["schema_json"]) if job.get("schema_json") else None
        mode = settings.gemini_prompt_delivery_mode
        if mode not in {"text", "file", "auto"}:
            raise RuntimeError(f"invalid_gemini_prompt_delivery_mode:{mode}")
        use_file = mode == "file" or (
            mode == "auto"
            and bool(settings.gemini_file_input_selector)
            and len(prompt) >= settings.file_upload_threshold_chars
        )
        if use_file:
            upload = self._prepare_gemini_upload(job)
            file_inputs = page.locator(settings.gemini_file_input_selector)
            if await file_inputs.count() == 0 and settings.gemini_upload_open_selector:
                await page.locator(settings.gemini_upload_open_selector).last.click(
                    timeout=settings.selector_timeout_ms
                )
                await file_inputs.last.wait_for(
                    state="attached", timeout=settings.selector_timeout_ms
                )
            await file_inputs.last.set_input_files(str(upload))
            log_event(
                "gemini_fallback_file_uploaded",
                job_id=job["id"],
                request_id=job["request_id"],
                file=upload.name,
            )
            expected = response_filename(job["id"])
            composer_text = "\n\n".join([
                settings.gemini_file_prompt_instruction,
                _schema_turn_binding(job, schema),
                delivery_instruction(
                    mode=settings.gemini_response_mode,
                    filename=expected,
                ),
            ])
            return await self._a_gemini_composer_set(page, composer_text)
        return await self._a_gemini_composer_set(page, prompt)

    async def _a_gemini_send(
        self,
        page: Any,
        composer: Any,
        job: dict[str, Any],
        *,
        mark_sent: bool,
        repair: bool = False,
    ) -> None:
        if settings.gemini_send_selector:
            await page.locator(settings.gemini_send_selector).last.click(
                timeout=settings.selector_timeout_ms
            )
        else:
            await composer.press(settings.keyboard_send)
        if mark_sent and not repair:
            job_store.mark_sent(job["id"])
        log_event(
            "gemini_fallback_schema_repair_sent"
            if repair
            else "gemini_fallback_prompt_sent",
            job_id=job["id"],
            request_id=job["request_id"],
        )

    async def _a_gemini_is_generating(self, page: Any) -> bool:
        if not settings.gemini_stop_generating_selector:
            return False
        try:
            return await page.locator(
                settings.gemini_stop_generating_selector
            ).last.is_visible(timeout=250)
        except Exception:
            return False

    async def _a_gemini_stop_generation(
        self, page: Any, job: dict[str, Any], reason: str
    ) -> None:
        if not settings.gemini_stop_generating_selector:
            return
        try:
            button = page.locator(settings.gemini_stop_generating_selector).last
            if await button.is_visible(timeout=250):
                await button.click(timeout=1000)
                log_event(
                    "gemini_fallback_generation_cancelled",
                    job_id=job["id"],
                    request_id=job["request_id"],
                    reason=reason,
                )
        except Exception as exc:
            log_event(
                "gemini_fallback_generation_cancel_failed",
                job_id=job["id"],
                request_id=job["request_id"],
                reason=reason,
                error=f"{type(exc).__name__}:{exc}",
            )

    async def _a_gemini_attachment_count(self, page: Any) -> int:
        if not settings.gemini_download_open_selector:
            return 0
        try:
            return await page.locator(settings.gemini_download_open_selector).count()
        except Exception:
            return 0

    async def _a_gemini_baseline(self, page: Any) -> tuple[int, str, int]:
        messages = page.locator(settings.gemini_assistant_message_selector)
        count = await messages.count()
        latest = ""
        if count:
            try:
                latest = (await messages.last.inner_text(timeout=1000)).strip()
            except Exception:
                pass
        return count, latest, await self._a_gemini_attachment_count(page)

    async def _a_gemini_download_locator(self, page: Any) -> tuple[Any, Any]:
        selector = settings.gemini_download_link_selector
        if not selector:
            raise RuntimeError(
                "download_response_requested_but_GEMINI_DOWNLOAD_LINK_SELECTOR_empty"
            )
        deadline = time.monotonic() + settings.download_timeout_ms / 1000.0
        while time.monotonic() < deadline:
            pages = list(page.context.pages)
            for candidate_page in reversed(pages):
                for frame in reversed(candidate_page.frames):
                    try:
                        locator = frame.locator(selector).last
                        if await locator.count() and await locator.is_visible(timeout=250):
                            return candidate_page, locator
                    except Exception:
                        continue
            await asyncio.sleep(settings.browser_poll_ms / 1000.0)
        raise TimeoutError("gemini_download_control_not_found")

    async def _a_gemini_download_response(
        self, page: Any, job: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        scope_selector = (
            settings.gemini_download_scope_selector
            or settings.gemini_assistant_message_selector
        )
        newest = page.locator(scope_selector).last
        if settings.gemini_download_open_selector:
            opener = newest.locator(settings.gemini_download_open_selector).last
            if not await opener.count():
                opener = page.locator(settings.gemini_download_open_selector).last
            await opener.click(
                timeout=min(settings.selector_timeout_ms, settings.download_timeout_ms)
            )
        download_page, selector = await self._a_gemini_download_locator(page)
        async with download_page.expect_download(
            timeout=settings.download_timeout_ms
        ) as info:
            await selector.click(timeout=settings.selector_timeout_ms)
        download = await info.value
        suggested = download.suggested_filename or "response.json"
        suffix = Path(suggested).suffix.lower() or ".json"
        if suffix not in settings.allowed_download_extensions:
            raise RuntimeError(f"download_extension_not_allowed:{suffix}")
        if settings.require_response_filename and not filename_matches_job(
            suggested, job["id"]
        ):
            log_event(
                "gemini_fallback_download_identity_mismatch",
                job_id=job["id"],
                request_id=job["request_id"],
                suggested=suggested,
                expected_token=response_filename_token(job["id"]),
            )
            raise RuntimeError(
                "download_identity_mismatch:"
                f"expected_token={response_filename_token(job['id'])}:got={suggested}"
            )
        target = settings.download_dir / (
            f"{_safe_name(job['request_id'])}-gemini-"
            f"{response_filename_token(job['id'])}{suffix}"
        )
        await download.save_as(str(target))
        log_event(
            "gemini_fallback_response_downloaded",
            job_id=job["id"],
            request_id=job["request_id"],
            file=target.name,
            suggested=suggested,
        )
        return read_response_file(target)

    async def _a_gemini_materialize_inline_response(
        self, job: dict[str, Any], current: str
    ) -> tuple[dict[str, Any], str]:
        obj, normalized = extract_json_object_text(current)
        target = settings.download_dir / (
            f"{_safe_name(job['request_id'])}-gemini-"
            f"{response_filename_token(job['id'])}.json"
        )
        target.write_text(normalized, encoding="utf-8")
        log_event(
            "gemini_fallback_inline_json_materialized",
            job_id=job["id"],
            request_id=job["request_id"],
            file=target.name,
            reason="provider_did_not_create_attachment",
        )
        # Re-enter through the same controlled-file size, location and JSON
        # checks used for a browser download. Schema validation follows in the
        # common completion path before the result can reach MT5.
        return read_response_file(target)

    async def _a_gemini_wait_response(
        self,
        page: Any,
        job: dict[str, Any],
        baseline_count: int,
        baseline_last: str,
        baseline_attachment_count: int,
    ) -> tuple[dict[str, Any], str]:
        if settings.gemini_response_mode not in {"text", "download", "auto"}:
            raise RuntimeError(
                f"unsupported_gemini_response_mode:{settings.gemini_response_mode}"
            )
        messages = page.locator(settings.gemini_assistant_message_selector)
        stable_since = 0.0
        last_text = ""
        observed_new = False
        deadline_at = float(job["deadline_at"])
        while time.time() < deadline_at and not self._stop.is_set():
            # This page belongs to Gemini. Applying ChatGPT's text markers here
            # can match the forwarded prompt itself and recursively classify the
            # fallback as another primary-provider rate limit. Gemini remains
            # bounded by the same absolute deadline and fails closed on its own
            # transport or schema errors.
            current_row = job_store.get(job["id"])
            if not current_row or current_row.get("status") in {"expired", "failed"}:
                await self._a_gemini_stop_generation(page, job, "job_no_longer_active")
                raise TimeoutError("gemini_job_cancelled_or_expired")
            try:
                count = await messages.count()
                current = await messages.last.inner_text(timeout=1000) if count else ""
                current = current.strip()
            except Exception:
                count, current = 0, ""
            attachment_count = await self._a_gemini_attachment_count(page)
            if (
                count > baseline_count
                or (current and current != baseline_last)
                or attachment_count > baseline_attachment_count
            ):
                observed_new = True
            if observed_new:
                progress = f"{count}:{attachment_count}:{current}"
                if progress != last_text:
                    last_text = progress
                    stable_since = time.monotonic()
                elif not await self._a_gemini_is_generating(page) and stable_since and (
                    time.monotonic() - stable_since
                ) * 1000 >= settings.response_stable_ms:
                    if settings.gemini_response_mode in {"text", "auto"} and current:
                        try:
                            return extract_json_object_text(current)
                        except Exception:
                            if settings.gemini_response_mode == "text":
                                raise
                    if settings.gemini_response_mode in {"download", "auto"}:
                        if attachment_count > baseline_attachment_count:
                            return await self._a_gemini_download_response(page, job)
                        if current:
                            return await self._a_gemini_materialize_inline_response(
                                job, current
                            )
                        raise RuntimeError(
                            "gemini_response_completed_without_attachment_or_json"
                        )
                    raise RuntimeError(
                        f"invalid_gemini_response_mode:{settings.gemini_response_mode}"
                    )
            await asyncio.sleep(settings.browser_poll_ms / 1000.0)
        await self._a_gemini_stop_generation(page, job, "absolute_deadline_exceeded")
        job_store.expire_due()
        raise TimeoutError("gemini_response_deadline_exceeded")

    async def _a_scroll_bottom(self, page: Any) -> None:
        try:
            if settings.scroll_container_selector:
                await page.locator(settings.scroll_container_selector).last.evaluate(
                    "el => { el.scrollTop = el.scrollHeight; }"
                )
            else:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

    async def _a_composer_set(self, page: Any, value: str) -> Any:
        composer = page.locator(settings.chat_input_selector).last
        await composer.wait_for(state="visible", timeout=settings.selector_timeout_ms)
        try:
            await composer.fill(value)
        except Exception:
            await composer.click()
            await composer.press("Control+A")
            await composer.press("Backspace")
            await composer.press_sequentially(value, delay=0)
        return composer

    async def _a_deliver(self, page: Any, job: dict[str, Any]) -> Any:
        prompt = str(job["prompt"])
        schema = json.loads(job["schema_json"]) if job.get("schema_json") else None
        mode = settings.prompt_delivery_mode
        if mode not in {"text", "file", "auto"}:
            raise RuntimeError(f"invalid_prompt_delivery_mode:{mode}")
        use_file = mode == "file" or (
            mode == "auto" and bool(settings.file_input_selector) and len(prompt) >= settings.file_upload_threshold_chars
        )
        if use_file:
            if not settings.file_input_selector:
                raise RuntimeError("file_delivery_requested_but_BROWSER_FILE_INPUT_SELECTOR_empty")
            upload = self._prepare_upload(job)
            await page.locator(settings.file_input_selector).last.set_input_files(str(upload))
            log_event("browser_file_uploaded", job_id=job["id"], request_id=job["request_id"], file=upload.name)
            expected = response_filename(job["id"])
            composer_text = (
                f"{settings.file_prompt_instruction}\n\n"
                f"{_schema_turn_binding(job, schema)}\n\n"
                f"NAME THE ATTACHED FILE EXACTLY: {expected}\n"
                "That exact filename is required. A file under any other name is rejected."
            )
            return await self._a_composer_set(page, composer_text)
        return await self._a_composer_set(page, prompt)

    async def _a_send(self, page: Any, composer: Any, job: dict[str, Any], *, repair: bool = False) -> None:
        if settings.send_selector:
            await page.locator(settings.send_selector).last.click(timeout=settings.selector_timeout_ms)
        else:
            await composer.press(settings.keyboard_send)
        if not repair:
            job_store.mark_sent(job["id"])
        log_event(
            "browser_schema_repair_sent" if repair else "browser_prompt_sent",
            job_id=job["id"],
            request_id=job["request_id"],
            send_mode="selector" if settings.send_selector else "keyboard",
        )

    async def _a_is_generating(self, page: Any) -> bool:
        if not settings.stop_generating_selector:
            return False
        try:
            return await page.locator(settings.stop_generating_selector).last.is_visible(timeout=250)
        except Exception:
            return False

    async def _a_stop_generation(self, page: Any, job: dict[str, Any], reason: str) -> None:
        if settings.stop_generating_selector:
            try:
                button = page.locator(settings.stop_generating_selector).last
                if await button.is_visible(timeout=250):
                    await button.click(timeout=1000)
                    log_event(
                        "browser_generation_cancelled",
                        job_id=job["id"],
                        request_id=job["request_id"],
                        reason=reason,
                    )
            except Exception as exc:
                log_event(
                    "browser_generation_cancel_failed",
                    job_id=job["id"],
                    request_id=job["request_id"],
                    reason=reason,
                    error=f"{type(exc).__name__}:{exc}",
                )

    async def _a_download_response(self, page: Any, job: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if not settings.download_link_selector:
            raise RuntimeError("download_response_requested_but_BROWSER_DOWNLOAD_LINK_SELECTOR_empty")
        scope_selector = settings.download_scope_selector or settings.assistant_message_selector
        newest = page.locator(scope_selector).last
        if settings.download_open_selector:
            await newest.locator(settings.download_open_selector).last.click(timeout=settings.selector_timeout_ms)
            await page.wait_for_selector(settings.download_link_selector, timeout=settings.selector_timeout_ms)
            selector = page.locator(settings.download_link_selector).last
        else:
            selector = newest.locator(settings.download_link_selector).last
        async with page.expect_download(timeout=settings.download_timeout_ms) as info:
            await selector.click()
        download = await info.value
        suggested = download.suggested_filename or "response.json"
        suffix = Path(suggested).suffix.lower() or ".json"
        if suffix not in settings.allowed_download_extensions:
            raise RuntimeError(f"download_extension_not_allowed:{suffix}")
        if settings.require_response_filename and not filename_matches_job(suggested, job["id"]):
            log_event(
                "browser_download_identity_mismatch",
                job_id=job["id"],
                request_id=job["request_id"],
                suggested=suggested,
                expected_token=response_filename_token(job["id"]),
            )
            raise RuntimeError(
                "download_identity_mismatch:"
                f"expected_token={response_filename_token(job['id'])}:got={suggested}"
            )
        target = settings.download_dir / f"{_safe_name(job['request_id'])}-{response_filename_token(job['id'])}{suffix}"
        await download.save_as(str(target))
        log_event(
            "browser_response_downloaded",
            job_id=job["id"],
            request_id=job["request_id"],
            file=target.name,
            suggested=suggested,
        )
        return read_response_file(target)

    async def _a_wait_response(
        self,
        page: Any,
        job: dict[str, Any],
        baseline_count: int,
        baseline_last: str,
    ) -> tuple[dict[str, Any], str]:
        messages = page.locator(settings.assistant_message_selector)
        stable_since = 0.0
        last_text = ""
        observed_new = False
        deadline_at = float(job["deadline_at"])
        while time.time() < deadline_at and not self._stop.is_set():
            current_row = job_store.get(job["id"])
            if not current_row or current_row.get("status") in {"expired", "failed"}:
                await self._a_stop_generation(page, job, "job_no_longer_active")
                raise TimeoutError("browser_job_cancelled_or_expired")
            await self._a_scroll_bottom(page)
            try:
                count = await messages.count()
                current = await messages.last.inner_text(timeout=1000) if count else ""
                current = current.strip()
            except Exception:
                count, current = 0, ""
            if count > baseline_count or (current and current != baseline_last):
                observed_new = True
            if observed_new and current:
                if current != last_text:
                    last_text = current
                    stable_since = time.monotonic()
                elif not await self._a_is_generating(page) and stable_since and (
                    time.monotonic() - stable_since
                ) * 1000 >= settings.response_stable_ms:
                    if settings.response_mode in {"text", "auto"}:
                        try:
                            return extract_json_object_text(current)
                        except Exception:
                            if settings.response_mode == "text":
                                raise
                    if settings.response_mode in {"download", "auto"}:
                        # ChatGPT occasionally renders the exact JSON inline
                        # instead of creating the requested file. A fresh
                        # conversation plus strict schema validation makes the
                        # current turn safe to accept without relaxing any
                        # decision or identity contract.
                        if settings.response_mode == "download":
                            try:
                                inline = extract_json_object_text(current)
                                log_event(
                                    "browser_inline_json_fallback",
                                    job_id=job["id"],
                                    request_id=job["request_id"],
                                )
                                return inline
                            except Exception:
                                pass
                        return await self._a_download_response(page, job)
                    raise RuntimeError(f"invalid_response_mode:{settings.response_mode}")
            await asyncio.sleep(settings.browser_poll_ms / 1000.0)
        await self._a_stop_generation(page, job, "absolute_deadline_exceeded")
        job_store.expire_due()
        raise TimeoutError("browser_response_deadline_exceeded")

    async def _a_baseline(self, page: Any) -> tuple[int, str]:
        messages = page.locator(settings.assistant_message_selector)
        count = await messages.count()
        latest = ""
        if count:
            try:
                latest = (await messages.last.inner_text(timeout=1000)).strip()
            except Exception:
                pass
        return count, latest

    async def _a_process_job(
        self,
        lane: _AsyncChatTabLane,
        job: dict[str, Any],
        active_roots: dict[str, int],
        totals: dict[str, int],
        *,
        gemini_lane: _AsyncChatTabLane | None = None,
    ) -> bool:
        page = lane.page
        request_id = str(job["request_id"])
        self._parallel_state(
            None,
            active_roots,
            totals,
            active_tab=lane.number,
            current_url=page.url,
            event="job_processing",
            error="",
        )
        sent = bool(job.get("sent_at"))
        provider_used = "chatgpt"
        try:
            if time.time() >= float(job["deadline_at"]):
                job_store.expire_due()
                raise TimeoutError("deadline_exceeded_before_browser_send")
            profile = profile_by_alias(str(job["chat_alias"]))
            if not profile:
                raise RuntimeError(f"chat_profile_not_found:{job['chat_alias']}")
            if sent:
                raise RuntimeError("job_already_marked_sent_no_automatic_resubmit")
            try:
                if lane.fallback_active:
                    if gemini_lane is None:
                        raise RuntimeError("gemini_fallback_affinity_active_but_unavailable")
                    provider_used = "gemini"
                    page = gemini_lane.page
                    (
                        baseline_count,
                        baseline_last,
                        baseline_attachment_count,
                    ) = await self._a_gemini_baseline(page)
                    composer = await self._a_gemini_deliver(page, job)
                    await self._a_gemini_send(
                        page, composer, job, mark_sent=True
                    )
                    sent = True
                    obj, normalized = await self._a_gemini_wait_response(
                        page,
                        job,
                        baseline_count,
                        baseline_last,
                        baseline_attachment_count,
                    )
                else:
                    await page.bring_to_front()
                    await self._a_apply_profile_preferences(page, profile)
                    rate_limit_marker = await self._a_primary_rate_limit_marker(page)
                    if rate_limit_marker:
                        raise BrowserProviderRateLimit(
                            "chatgpt", rate_limit_marker, after_send=False
                        )
                    baseline_count, baseline_last = await self._a_baseline(page)
                    composer = await self._a_deliver(page, job)
                    rate_limit_marker = await self._a_primary_rate_limit_marker(page)
                    if rate_limit_marker:
                        raise BrowserProviderRateLimit(
                            "chatgpt", rate_limit_marker, after_send=False
                        )
                    await self._a_send(page, composer, job)
                    sent = True
                    obj, normalized = await self._a_wait_response(
                        page, job, baseline_count, baseline_last
                    )
            except BrowserProviderRateLimit as rate_limit:
                if gemini_lane is None:
                    raise RuntimeError(
                        "explicit_chatgpt_rate_limit_but_gemini_fallback_unavailable:"
                        f"{rate_limit.marker}"
                    ) from rate_limit
                lane.fallback_active = True
                provider_used = "gemini"
                log_event(
                    "browser_provider_fallback_started",
                    request_id=request_id,
                    job_id=job["id"],
                    from_provider="chatgpt",
                    to_provider="gemini",
                    marker=rate_limit.marker,
                    primary_prompt_sent=rate_limit.after_send,
                    same_request_id=True,
                    same_schema=True,
                )
                await self._a_start_gemini_conversation(gemini_lane, job)
                page = gemini_lane.page
                (
                    baseline_count,
                    baseline_last,
                    baseline_attachment_count,
                ) = await self._a_gemini_baseline(page)
                composer = await self._a_gemini_deliver(page, job)
                await self._a_gemini_send(
                    page, composer, job, mark_sent=not sent
                )
                sent = True
                obj, normalized = await self._a_gemini_wait_response(
                    page,
                    job,
                    baseline_count,
                    baseline_last,
                    baseline_attachment_count,
                )
            if len(normalized.encode("utf-8")) > settings.max_response_bytes:
                raise RuntimeError("response_too_large")
            schema = json.loads(job["schema_json"]) if job.get("schema_json") else None
            if schema:
                errors = _schema_errors(schema, obj)
                if errors:
                    details = _schema_error_details(errors)
                    log_event(
                        "browser_response_schema_invalid",
                        job_id=job["id"],
                        request_id=request_id,
                        error_count=len(errors),
                        errors=details[:40],
                    )
                    repaired = False
                    for repair_attempt in range(settings.schema_repair_max_attempts):
                        if provider_used == "gemini":
                            (
                                baseline_count,
                                baseline_last,
                                baseline_attachment_count,
                            ) = await self._a_gemini_baseline(page)
                            repair_composer = await self._a_gemini_composer_set(
                                page,
                                _schema_repair_prompt(
                                    job,
                                    schema,
                                    errors,
                                    response_mode=settings.gemini_response_mode,
                                ),
                            )
                            await self._a_gemini_send(
                                page,
                                repair_composer,
                                job,
                                mark_sent=False,
                                repair=True,
                            )
                            obj, normalized = await self._a_gemini_wait_response(
                                page,
                                job,
                                baseline_count,
                                baseline_last,
                                baseline_attachment_count,
                            )
                        else:
                            baseline_count, baseline_last = await self._a_baseline(page)
                            repair_composer = await self._a_composer_set(
                                page, _schema_repair_prompt(job, schema, errors)
                            )
                            await self._a_send(page, repair_composer, job, repair=True)
                            obj, normalized = await self._a_wait_response(
                                page, job, baseline_count, baseline_last
                            )
                        errors = _schema_errors(schema, obj)
                        if not errors:
                            repaired = True
                            log_event(
                                "browser_schema_repair_completed",
                                job_id=job["id"],
                                request_id=request_id,
                                attempt=repair_attempt + 1,
                            )
                            break
                        details = _schema_error_details(errors)
                    if not repaired:
                        raise RuntimeError(
                            f"structured_response_schema_invalid:{len(errors)}_errors:"
                            + " | ".join(details[:4])
                        )
            job_store.submit(job["id"], normalized, reviewed=False)
            log_event(
                "browser_job_completed",
                job_id=job["id"],
                request_id=request_id,
                tab=lane.number,
                provider=provider_used,
                response_mode=(
                    settings.gemini_response_mode
                    if provider_used == "gemini"
                    else settings.response_mode
                ),
            )
            return True
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            job_store.fail(job["id"], error)
            log_event(
                "browser_job_failed",
                job_id=job["id"],
                request_id=request_id,
                tab=lane.number,
                sent=sent,
                error=error,
            )
            self._parallel_state(
                None, active_roots, totals, active_tab=lane.number,
                event="job_failed", error=error,
            )
            return False

    def _open_chat_tabs(
        self,
        context: Any,
        first_page: Any,
        urls: list[str],
    ) -> _ChatTabRotation:
        pages = [first_page] + [page for page in context.pages if page is not first_page]
        while len(pages) < len(urls):
            pages.append(context.new_page())
        # The persistent profile is dedicated to this bridge.  Close restored
        # surplus pages so the runtime really owns exactly the configured X tabs.
        for extra in pages[len(urls):]:
            try:
                extra.close()
            except Exception:
                pass
        slots: list[_ChatTabSlot] = []
        for index, (page, url) in enumerate(zip(pages[:len(urls)], urls), start=1):
            if url and url != "about:blank":
                self._navigate(page, url)
            slots.append(
                _ChatTabSlot(
                    page=page,
                    number=index,
                    bound_alias=(
                        settings.default_chat_alias
                        if url and url != "about:blank"
                        else ""
                    ),
                )
            )
            log_event(
                "browser_chat_tab_opened",
                tab=index,
                initial_url=url,
                current_url=page.url,
            )
        slots[0].page.bring_to_front()
        return _ChatTabRotation(slots, settings.browser_requests_per_tab)

    def _drain_commands(self, slot: _ChatTabSlot) -> None:
        while True:
            try:
                command, value = browser_state.commands.get_nowait()
            except Exception:
                return
            if command == "open" and value:
                slot.page.goto(value, wait_until="domcontentloaded")
                # The dashboard command carries a URL, not an alias. Force the
                # next queued job to re-bind the managed tab explicitly.
                slot.bound_alias = ""
                browser_state.update(current_url=slot.page.url, last_event="manual_navigation")

    def _navigate(self, page: Any, url: str) -> None:
        if not url:
            raise RuntimeError("registered_chat_url_missing")
        if page.url != url:
            page.goto(url, wait_until="domcontentloaded", timeout=settings.navigation_timeout_ms)
        if settings.login_ready_selector:
            page.locator(settings.login_ready_selector).first.wait_for(state="visible", timeout=settings.selector_timeout_ms)
        browser_state.update(current_url=page.url)

    def _apply_profile_preferences(self, page: Any, profile: Any) -> None:
        if settings.model_control_selector and settings.model_option_selector_template and profile.desired_model:
            page.locator(settings.model_control_selector).last.click()
            page.locator(_selector_value(settings.model_option_selector_template, profile.desired_model)).last.click()
        if settings.effort_control_selector and settings.effort_option_selector_template and profile.desired_effort:
            page.locator(settings.effort_control_selector).last.click()
            page.locator(_selector_value(settings.effort_option_selector_template, profile.desired_effort)).last.click()

    def _scroll_bottom(self, page: Any) -> None:
        try:
            if settings.scroll_container_selector:
                loc = page.locator(settings.scroll_container_selector).last
                loc.evaluate("el => { el.scrollTop = el.scrollHeight; }")
            else:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass

    def _composer_set(self, page: Any, text: str) -> Any:
        composer = page.locator(settings.chat_input_selector).last
        composer.wait_for(state="visible", timeout=settings.selector_timeout_ms)
        try:
            composer.fill(text)
        except Exception:
            composer.click()
            composer.press("Control+A")
            composer.press("Backspace")
            composer.press_sequentially(text, delay=0)
        return composer

    def _prepare_upload(self, job: dict[str, Any]) -> Path:
        schema = json.loads(job["schema_json"]) if job.get("schema_json") else None
        expected = response_filename(job["id"])
        payload = {
            "bridge_request_id": job["request_id"],
            "instructions": job["prompt"],
            "response_schema": schema,
            # Named twice on purpose: as data the model can read back, and inside
            # the prose contract it is judged against.
            "required_response_filename": expected,
            # Must agree with the delivery clause inside ``instructions``.  A
            # hardcoded "no other text" here used to contradict the download
            # mode and left the model choosing between two orders.
            "response_requirement": delivery_instruction(filename=expected),
        }
        filename = f"{_safe_name(job['request_id'])}-{job['id'][:8]}.json"
        path = settings.upload_dir / filename
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path

    def _deliver(self, page: Any, job: dict[str, Any]) -> Any:
        prompt = str(job["prompt"])
        schema = json.loads(job["schema_json"]) if job.get("schema_json") else None
        mode = settings.prompt_delivery_mode
        if mode not in {"text", "file", "auto"}:
            raise RuntimeError(f"invalid_prompt_delivery_mode:{mode}")
        use_file = mode == "file" or (
            mode == "auto" and bool(settings.file_input_selector) and len(prompt) >= settings.file_upload_threshold_chars
        )
        if use_file:
            if not settings.file_input_selector:
                raise RuntimeError("file_delivery_requested_but_BROWSER_FILE_INPUT_SELECTOR_empty")
            upload = self._prepare_upload(job)
            file_input = page.locator(settings.file_input_selector).last
            file_input.set_input_files(str(upload))
            log_event("browser_file_uploaded", job_id=job["id"], request_id=job["request_id"], file=upload.name)
            expected = response_filename(job["id"])
            # The composer text is the message the model reads last and most
            # directly, so the exact name is restated here rather than left to
            # the uploaded envelope alone.
            composer_text = (
                f"{settings.file_prompt_instruction}\n\n"
                f"{_schema_turn_binding(job, schema)}\n\n"
                f"NAME THE ATTACHED FILE EXACTLY: {expected}\n"
                "That exact filename is required. A file under any other name is rejected as "
                "a stale answer from an earlier message, whatever it contains."
            )
            return self._composer_set(page, composer_text)
        return self._composer_set(page, prompt)

    def _send_schema_repair(self, page: Any, job: dict[str, Any], prompt: str) -> None:
        composer = self._composer_set(page, prompt)
        if settings.send_selector:
            page.locator(settings.send_selector).last.click(timeout=settings.selector_timeout_ms)
        else:
            composer.press(settings.keyboard_send)
        log_event(
            "browser_schema_repair_sent",
            job_id=job["id"],
            request_id=job["request_id"],
        )

    def _send(self, page: Any, composer: Any, job: dict[str, Any]) -> None:
        if settings.send_selector:
            page.locator(settings.send_selector).last.click(timeout=settings.selector_timeout_ms)
        else:
            composer.press(settings.keyboard_send)
        job_store.mark_sent(job["id"])
        log_event("browser_prompt_sent", job_id=job["id"], request_id=job["request_id"], send_mode="selector" if settings.send_selector else "keyboard")

    def _is_generating(self, page: Any) -> bool:
        if not settings.stop_generating_selector:
            return False
        try:
            return page.locator(settings.stop_generating_selector).last.is_visible(timeout=250)
        except Exception:
            return False

    def _download_response(self, page: Any, job: dict[str, Any]) -> tuple[dict[str, Any], str]:
        if not settings.download_link_selector:
            raise RuntimeError("download_response_requested_but_BROWSER_DOWNLOAD_LINK_SELECTOR_empty")
        # Scope to the newest assistant turn. A page-wide match would silently
        # click an earlier answer's download control whenever the current answer
        # produced no file, binding a stale decision to this request. Failing
        # here is correct: ai_gate.py fails closed rather than trade on it.
        scope_selector = settings.download_scope_selector or settings.assistant_message_selector
        newest = page.locator(scope_selector).last
        if settings.download_open_selector:
            # Two-step UIs: open the produced file from the newest turn first.
            # Scoping this click is what keeps a stale answer from being served;
            # the panel it opens is page-level and belongs to the file just opened.
            newest.locator(settings.download_open_selector).last.click(
                timeout=settings.selector_timeout_ms
            )
            page.wait_for_selector(
                settings.download_link_selector, timeout=settings.selector_timeout_ms
            )
            selector = page.locator(settings.download_link_selector).last
        else:
            selector = newest.locator(settings.download_link_selector).last
        with page.expect_download(timeout=settings.download_timeout_ms) as info:
            selector.click()
        download = info.value
        suggested = download.suggested_filename or "response.json"
        suffix = Path(suggested).suffix.lower() or ".json"
        if suffix not in settings.allowed_download_extensions:
            raise RuntimeError(f"download_extension_not_allowed:{suffix}")
        # Identity check, not a formatting check.  The open/download controls are
        # looked up page-wide in the two-step flow, so a turn that produced no
        # file can silently yield an earlier answer's artifact -- observed as four
        # byte-identical downloads across four separate generations.  The model was
        # told to name this turn's file after its own job, so a name without that
        # nonce means the artifact is not this turn's answer.  Failing here is
        # correct: ai_gate.py fails closed rather than trade on a stale decision.
        if settings.require_response_filename and not filename_matches_job(suggested, job["id"]):
            log_event(
                "browser_download_identity_mismatch",
                job_id=job["id"],
                request_id=job["request_id"],
                suggested=suggested,
                expected_token=response_filename_token(job["id"]),
            )
            raise RuntimeError(
                "download_identity_mismatch:"
                f"expected_token={response_filename_token(job['id'])}:got={suggested}"
            )
        # Named after the job rather than a random uuid so a download can be traced
        # to the upload and audit line it belongs to.
        target = settings.download_dir / f"{_safe_name(job['request_id'])}-{response_filename_token(job['id'])}{suffix}"
        download.save_as(str(target))
        log_event(
            "browser_response_downloaded",
            job_id=job["id"],
            request_id=job["request_id"],
            file=target.name,
            suggested=suggested,
        )
        return read_response_file(target)

    def _wait_response(self, page: Any, job: dict[str, Any], baseline_count: int, baseline_last: str) -> tuple[dict[str, Any], str]:
        messages = page.locator(settings.assistant_message_selector)
        stable_since = 0.0
        last_text = ""
        observed_new = False
        deadline_at = float(job["deadline_at"])
        while time.time() < deadline_at and not self._stop.is_set():
            self._scroll_bottom(page)
            try:
                count = messages.count()
                current = messages.last.inner_text(timeout=1000).strip() if count else ""
            except Exception:
                count, current = 0, ""
            if count > baseline_count or (current and current != baseline_last):
                observed_new = True
            if observed_new and current:
                if current != last_text:
                    last_text = current
                    stable_since = time.monotonic()
                elif not self._is_generating(page) and stable_since and (time.monotonic() - stable_since) * 1000 >= settings.response_stable_ms:
                    if settings.response_mode in {"text", "auto"}:
                        try:
                            return extract_json_object_text(current)
                        except Exception:
                            if settings.response_mode == "text":
                                raise
                    if settings.response_mode in {"download", "auto"}:
                        return self._download_response(page, job)
                    raise RuntimeError(f"invalid_response_mode:{settings.response_mode}")
            time.sleep(settings.browser_poll_ms / 1000.0)
        raise TimeoutError("browser_response_deadline_exceeded")

    def _process_job(self, slot: _ChatTabSlot, job: dict[str, Any]) -> bool:
        page = slot.page
        request_id = str(job["request_id"])
        browser_state.update(
            active_request_id=request_id,
            active_chat_tab=slot.number,
            current_url=page.url,
            last_event="job_processing",
            error="",
        )
        sent = bool(job.get("sent_at"))
        submitted_now = False
        try:
            profile = profile_by_alias(str(job["chat_alias"]))
            if not profile:
                raise RuntimeError(f"chat_profile_not_found:{job['chat_alias']}")
            if sent:
                raise RuntimeError("job_already_marked_sent_no_automatic_resubmit")
            page.bring_to_front()
            # A managed tab stays on the conversation created from its new-chat
            # URL. Navigating back to the registered seed URL for every job
            # would collapse every tab into the same chat.
            if slot.bound_alias != profile.alias or page.url == "about:blank":
                self._navigate(page, profile.conversation_url)
                slot.bound_alias = profile.alias
            elif settings.login_ready_selector:
                page.locator(settings.login_ready_selector).first.wait_for(
                    state="visible",
                    timeout=settings.selector_timeout_ms,
                )
                browser_state.update(current_url=page.url)
            self._apply_profile_preferences(page, profile)
            messages = page.locator(settings.assistant_message_selector)
            baseline_count = messages.count()
            baseline_last = ""
            if baseline_count:
                try:
                    baseline_last = messages.last.inner_text(timeout=1000).strip()
                except Exception:
                    pass
            composer = self._deliver(page, job)
            self._send(page, composer, job)
            sent = True
            submitted_now = True
            obj, normalized = self._wait_response(page, job, baseline_count, baseline_last)
            if len(normalized.encode("utf-8")) > settings.max_response_bytes:
                raise RuntimeError("response_too_large")
            schema = json.loads(job["schema_json"]) if job.get("schema_json") else None
            if schema:
                # validate() raises only one error. Record every violation, so a
                # rejected review says what to correct instead of surfacing a
                # single arbitrary constraint.
                errors = _schema_errors(schema, obj)
                if errors:
                    details = _schema_error_details(errors)
                    log_event(
                        "browser_response_schema_invalid", job_id=job["id"],
                        request_id=request_id, error_count=len(errors), errors=details[:40],
                    )
                    repaired = False
                    for repair_attempt in range(settings.schema_repair_max_attempts):
                        messages = page.locator(settings.assistant_message_selector)
                        baseline_count = messages.count()
                        baseline_last = ""
                        if baseline_count:
                            try:
                                baseline_last = messages.last.inner_text(timeout=1000).strip()
                            except Exception:
                                pass
                        self._send_schema_repair(
                            page,
                            job,
                            _schema_repair_prompt(job, schema, errors),
                        )
                        obj, normalized = self._wait_response(
                            page, job, baseline_count, baseline_last
                        )
                        if len(normalized.encode("utf-8")) > settings.max_response_bytes:
                            raise RuntimeError("response_too_large")
                        errors = _schema_errors(schema, obj)
                        if not errors:
                            repaired = True
                            log_event(
                                "browser_schema_repair_completed",
                                job_id=job["id"],
                                request_id=request_id,
                                attempt=repair_attempt + 1,
                            )
                            break
                        details = _schema_error_details(errors)
                        log_event(
                            "browser_schema_repair_invalid",
                            job_id=job["id"],
                            request_id=request_id,
                            attempt=repair_attempt + 1,
                            error_count=len(errors),
                            errors=details[:40],
                        )
                    if not repaired:
                        raise RuntimeError(
                            f"structured_response_schema_invalid:{len(errors)}_errors:"
                            + " | ".join(details[:4])
                        )
            job_store.submit(job["id"], normalized, reviewed=False)
            browser_state.update(last_event="job_completed", error="")
            log_event("browser_job_completed", job_id=job["id"], request_id=request_id, response_mode=settings.response_mode)
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            # The original evidence request is never resent. A correction turn
            # can only repair a parsed JSON object's shape under the original
            # strict schema; any remaining ambiguity fails closed here.
            job_store.fail(job["id"], error)
            browser_state.update(last_event="job_failed", error=error)
            log_event("browser_job_failed", job_id=job["id"], request_id=request_id, sent=sent, error=error)
        finally:
            browser_state.update(active_request_id="")
        return submitted_now


autonomous_browser_worker = AutonomousBrowserWorker()
