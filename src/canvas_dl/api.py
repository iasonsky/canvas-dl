from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from .utils import parse_link_header


class CanvasAPIError(Exception):
    pass


class RateLimitExceeded(httpx.HTTPError):
    """Raised so tenacity retries when Canvas throttles us."""


@dataclass
class RateLimiter:
    """Polite throttle for Canvas's leaky-bucket limiter.

    Canvas exposes the remaining bucket quota via the ``X-Rate-Limit-Remaining``
    header (a course starts around ~700 and refills over time). When the bucket
    empties Canvas returns HTTP 403 with a body of ``Rate Limit Exceeded``
    (some proxies surface 429 instead). We (a) keep a minimum spacing between
    requests and (b) slow down proactively when the remaining quota gets low,
    so we never actually hit the wall.
    """

    min_interval: float = 0.0
    low_water: float = 150.0
    low_water_sleep: float = 0.5
    _last: float = field(default=0.0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def acquire(self) -> None:
        with self._lock:
            if self.min_interval > 0:
                now = time.monotonic()
                wait = self.min_interval - (now - self._last)
                if wait > 0:
                    time.sleep(wait)
            self._last = time.monotonic()

    def observe(self, resp: httpx.Response) -> None:
        remaining = resp.headers.get("X-Rate-Limit-Remaining")
        if remaining is None:
            return
        try:
            if float(remaining) < self.low_water:
                time.sleep(self.low_water_sleep)
        except ValueError:
            pass


def _is_rate_limited(resp: httpx.Response) -> bool:
    if resp.status_code == 429:
        return True
    # Canvas returns 403 with this exact body when the bucket is empty.
    if resp.status_code == 403 and "rate limit exceeded" in resp.text.lower():
        return True
    return False


@dataclass
class CanvasClient:
    base_url: str
    access_token: str
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    timeout: float = 30.0
    _client: Optional[httpx.Client] = field(default=None, init=False, repr=False)

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=self._headers(),
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "CanvasClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @retry(
        reraise=True,
        stop=stop_after_attempt(5),
        wait=wait_exponential_jitter(initial=0.5, max=10.0),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _request(self, method: str, url: str, params: Optional[dict] = None) -> httpx.Response:
        self.rate_limiter.acquire()
        resp = self.http.request(method, url, params=params)

        if _is_rate_limited(resp):
            # Retry-After may be absent, numeric, or an HTTP-date — don't crash.
            try:
                retry_after = float(resp.headers.get("Retry-After", "2") or "2")
            except ValueError:
                retry_after = 2.0
            time.sleep(min(max(retry_after, 1.0), 30.0))
            raise RateLimitExceeded("Canvas rate limit hit; backing off")

        self.rate_limiter.observe(resp)

        if resp.status_code >= 400:
            raise CanvasAPIError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        return resp

    def _paginate(self, path: str, params: Optional[dict] = None) -> List[dict]:
        url = path if path.startswith("http") else f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        collected: List[dict] = []
        next_url: Optional[str] = url
        first = True
        while next_url:
            resp = self._request("GET", next_url, params=params if first else None)
            first = False
            try:
                data = resp.json()
            except Exception:
                break
            if isinstance(data, list):
                collected.extend(data)
            else:
                collected.append(data)
            links = parse_link_header(resp.headers.get("Link"))
            next_url = links.get("next")
        return collected

    # ------------------------------------------------------------------ #
    # High-level endpoints
    # ------------------------------------------------------------------ #
    def list_courses(
        self, enrollment_state: Optional[str] = None, published: Optional[bool] = None
    ) -> List[dict]:
        params: Dict[str, Any] = {"per_page": 100, "include[]": "term"}
        if enrollment_state:
            params["enrollment_state"] = enrollment_state
        if published is not None:
            params["published"] = str(published).lower()
        return self._paginate("/courses", params=params)

    def list_modules(self, course_id: int) -> List[dict]:
        params = {"per_page": 100, "include[]": "items"}
        return self._paginate(f"/courses/{course_id}/modules", params=params)

    def list_module_items(self, course_id: int, module_id: int) -> List[dict]:
        params = {"per_page": 100}
        return self._paginate(f"/courses/{course_id}/modules/{module_id}/items", params=params)

    def list_files(self, course_id: int) -> List[dict]:
        """All files in a course (not just those linked from modules)."""
        return self._paginate(f"/courses/{course_id}/files", params={"per_page": 100})

    def list_folders(self, course_id: int) -> List[dict]:
        """All folders in a course; ``full_name`` gives the path from root."""
        return self._paginate(f"/courses/{course_id}/folders", params={"per_page": 100})

    def list_assignments(self, course_id: int) -> List[dict]:
        params = {"per_page": 100, "include[]": "submission"}
        return self._paginate(f"/courses/{course_id}/assignments", params=params)

    def get_file_info(self, file_id: int) -> dict:
        url = f"{self.base_url.rstrip('/')}/files/{file_id}"
        return self._request("GET", url).json()

    def get_course_file_info(self, course_id: int, file_id: int) -> dict:
        url = f"{self.base_url.rstrip('/')}/courses/{course_id}/files/{file_id}"
        return self._request("GET", url).json()
