"""Optional browser rendering for configured JavaScript-only sources."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class BrowserRenderError(RuntimeError):
    """Raised when a configured browser fallback cannot produce HTML."""


@dataclass(frozen=True, slots=True)
class RenderedPage:
    """One browser-rendered page returned to a collector."""

    html: str
    final_url: str
    http_status: int | None = None


class BrowserRenderer(Protocol):
    """Small interface that keeps collectors testable without a real browser."""

    def render(
        self,
        url: str,
        *,
        timeout_seconds: float,
        wait_selector: str | None,
        minimum_text_characters: int,
        settle_milliseconds: int,
    ) -> RenderedPage:
        """Render one URL and return the post-hydration HTML."""

    def close(self) -> None:
        """Release browser resources."""


class PlaywrightBrowserRenderer:
    """Lazy, reusable headless Chromium renderer."""

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None

    def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserRenderError(
                "Playwright is not installed; rebuild .venv from docs/requirement.txt"
            ) from exc
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
        except Exception as exc:
            self.close()
            raise BrowserRenderError(
                "Chromium is unavailable; run: python -m playwright install chromium"
            ) from exc
        return self._browser

    def render(
        self,
        url: str,
        *,
        timeout_seconds: float,
        wait_selector: str | None,
        minimum_text_characters: int,
        settle_milliseconds: int,
    ) -> RenderedPage:
        browser = self._ensure_browser()
        timeout_ms = max(1, int(timeout_seconds * 1_000))
        context = None
        try:
            context = browser.new_context()
            page = context.new_page()
            response = page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            if wait_selector:
                page.wait_for_selector(
                    wait_selector,
                    state="attached",
                    timeout=timeout_ms,
                )
            if minimum_text_characters > 0:
                page.wait_for_function(
                    """minimum => {
                        const root = document.querySelector('main, #root, #app, body');
                        return !!root && (root.innerText || '').trim().length >= minimum;
                    }""",
                    arg=minimum_text_characters,
                    timeout=timeout_ms,
                )
            if settle_milliseconds > 0:
                page.wait_for_timeout(settle_milliseconds)
            return RenderedPage(
                html=page.content(),
                final_url=page.url,
                http_status=response.status if response is not None else None,
            )
        except Exception as exc:
            raise BrowserRenderError(f"browser render failed for {url}: {exc}") from exc
        finally:
            if context is not None:
                context.close()

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._browser = None
        if self._playwright is not None:
            self._playwright.stop()
            self._playwright = None


__all__ = [
    "BrowserRenderError",
    "BrowserRenderer",
    "PlaywrightBrowserRenderer",
    "RenderedPage",
]
