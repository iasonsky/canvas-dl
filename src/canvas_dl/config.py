from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib  # py311+
except Exception:  # pragma: no cover
    import tomli as tomllib  # type: ignore

try:
    import tomli_w  # type: ignore
except Exception:  # pragma: no cover
    tomli_w = None  # type: ignore

from dotenv import load_dotenv
from platformdirs import PlatformDirs

from .utils import ensure_dir, restrict_permissions

DEFAULT_API_URL = "https://canvas.uva.nl/api/v1"


@dataclass
class AppConfig:
    api_url: str = DEFAULT_API_URL
    access_token: Optional[str] = None
    concurrency: int = 3
    verbose: bool = False

    @classmethod
    def from_sources(cls, env: dict[str, str] | None = None) -> "AppConfig":
        # Load .env first
        load_dotenv(override=False)
        env = env or os.environ  # type: ignore

        # Config file
        dirs = PlatformDirs(appname="canvas-dl", appauthor=False)
        config_path = Path(dirs.user_config_dir) / "config.toml"
        file_cfg: dict = {}
        if config_path.exists():
            try:
                with config_path.open("rb") as f:
                    file_cfg = tomllib.load(f)
            except Exception:
                file_cfg = {}

        api_url = env.get("API_URL") or file_cfg.get("api_url") or DEFAULT_API_URL
        token = env.get("ACCESS_TOKEN") or file_cfg.get("access_token")

        concurrency_env = env.get("CANVAS_DL_CONCURRENCY") or str(file_cfg.get("concurrency", ""))
        try:
            concurrency = int(concurrency_env) if concurrency_env else 3
        except ValueError:
            concurrency = 3

        verbose = (env.get("CANVAS_DL_VERBOSE") or str(file_cfg.get("verbose", "false"))).lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        return cls(api_url=api_url, access_token=token, concurrency=concurrency, verbose=verbose)

    @staticmethod
    def config_path() -> Path:
        dirs = PlatformDirs(appname="canvas-dl", appauthor=False)
        return Path(dirs.user_config_dir) / "config.toml"

    def save(self) -> None:
        path = self.config_path()
        ensure_dir(path.parent)
        data = {
            "api_url": self.api_url,
            "access_token": self.access_token,
            "concurrency": self.concurrency,
            "verbose": self.verbose,
        }
        if tomli_w is None:  # simple writer
            content = "\n".join(
                f"{k} = {repr(v).lower() if isinstance(v, bool) else repr(v)}" for k, v in data.items()
            )
            path.write_text(content, encoding="utf-8")
        else:
            with path.open("wb") as f:  # type: ignore
                f.write(tomli_w.dumps(data).encode("utf-8"))  # type: ignore
        # The file holds the access token — keep it owner-only.
        restrict_permissions(path)
