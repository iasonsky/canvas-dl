"""UI-agnostic logic behind the desktop GUI.

Imports no GUI toolkit so it is fully unit-testable. The view (``gui.py``)
owns widgets and marshals the progress callbacks onto the UI thread; everything
stateful and Canvas-related lives here.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Callable, List, Optional

from .api import CanvasClient, RateLimiter
from .config import DEFAULT_API_URL, AppConfig
from .content import DownloadOptions
from .download import DownloadResult, ProgressEvent, download_course
from .utils import TTLCache, get_app_dirs, sanitize_filename


def sanitize_course_dir(name: str) -> str:
    return sanitize_filename(name).rstrip(" .") or "course"


def build_options(
    *,
    sources: List[str],
    only: Optional[str] = None,
    name_glob: Optional[str] = None,
    concurrency: int = 3,
    instructions: bool = True,
    merge: bool = False,
    merge_scope: str = "per-module",
    zip_output: bool = False,
) -> DownloadOptions:
    return DownloadOptions(
        sources=DownloadOptions.parse_sources(",".join(sources) if sources else None),
        only_exts=[s.strip() for s in only.split(",")] if only else None,
        name_glob=name_glob or None,
        concurrency=max(1, int(concurrency)),
        include_assignment_instructions=instructions,
        merge_pdfs=merge,
        merge_scope=merge_scope,
        zip_output=zip_output,
    )


class GuiController:
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig.from_sources()
        self._client: Optional[CanvasClient] = None
        self._courses_cache: Optional[List[dict]] = None

    # -- auth/config ----------------------------------------------------- #
    def has_token(self) -> bool:
        return bool(self.config.access_token)

    @property
    def api_url(self) -> str:
        return self.config.api_url or DEFAULT_API_URL

    def save_token(self, token: str, api_url: Optional[str] = None) -> None:
        self.config.access_token = token.strip()
        if api_url:
            self.config.api_url = api_url.strip()
        self.config.save()
        self._client = None  # rebuild with new creds
        self._courses_cache = None

    def build_client(self) -> CanvasClient:
        if not self.config.access_token:
            raise ValueError("No access token configured.")
        if self._client is None:
            self._client = CanvasClient(
                base_url=self.api_url,
                access_token=self.config.access_token,
                rate_limiter=RateLimiter(min_interval=0.15),
            )
        return self._client

    def close(self) -> None:
        """Release the underlying HTTP client (call on GUI shutdown)."""
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- courses --------------------------------------------------------- #
    def load_courses(self, *, force: bool = False, published: bool = True) -> List[dict]:
        cache_path = Path(get_app_dirs().user_cache_dir) / "courses.json"
        cache = TTLCache(cache_path, ttl_seconds=300)
        if not force:
            cached = cache.load()
            if cached is not None:
                self._courses_cache = cached
                return cached
        courses = self.build_client().list_courses(published=published or None)
        cache.save(courses)
        self._courses_cache = courses
        return courses

    # -- download -------------------------------------------------------- #
    def start_download(
        self,
        *,
        course: dict,
        dest_root: Path,
        opts: DownloadOptions,
        on_event: Callable[[ProgressEvent], None],
        on_done: Callable[[DownloadResult], None],
        on_error: Callable[[Exception], None],
    ) -> threading.Thread:
        """Run a download on a background thread; return the thread (started)."""
        course_id = int(course["id"])
        course_name = course.get("name") or f"course-{course_id}"
        dest_dir = Path(dest_root).expanduser().resolve() / sanitize_course_dir(course_name)

        def run() -> None:
            try:
                client = self.build_client()
                result = asyncio.run(
                    download_course(client, course_id, course_name, dest_dir, opts, on_event)
                )
                on_done(result)
            except Exception as exc:  # noqa: BLE001 - surfaced to the UI
                on_error(exc)

        thread = threading.Thread(target=run, daemon=True, name="canvas-dl-download")
        thread.start()
        return thread
