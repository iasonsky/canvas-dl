from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import questionary
import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text

from . import __version__
from .api import CanvasAPIError, CanvasClient, RateLimiter
from .config import DEFAULT_API_URL, AppConfig
from .content import DownloadOptions
from .download import DownloadResult, ProgressEvent, download_course
from .utils import get_app_dirs, sanitize_filename

app = typer.Typer(add_completion=False)
console = Console()


def _build_client(cfg: AppConfig, api_url: Optional[str], token: Optional[str]) -> CanvasClient:
    base = api_url or cfg.api_url or DEFAULT_API_URL
    tok = token or cfg.access_token
    if not tok:
        raise typer.BadParameter(
            "Missing access token. Run 'canvas-dl auth' or set ACCESS_TOKEN/.env."
        )
    return CanvasClient(base_url=base, access_token=tok, rate_limiter=RateLimiter(min_interval=0.15))


class _CliReporter:
    """Render :class:`ProgressEvent` stream with rich (single-threaded, from the loop)."""

    def __init__(self) -> None:
        self.progress = Progress(
            TextColumn("{task.fields[filename]}", justify="left"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
            transient=True,
            refresh_per_second=8,
        )
        self.tasks: dict[str, int] = {}
        self.live = False

    def __call__(self, e: ProgressEvent) -> None:
        if e.kind == "phase":
            if self.live and e.phase != "download":
                self.progress.stop()
                self.live = False
            if e.message:
                console.print(f"[bold cyan]{e.message}[/bold cyan]")
            if e.phase == "download" and not self.live:
                self.progress.start()
                self.live = True
        elif e.kind == "file_start":
            self.tasks[e.key] = self.progress.add_task(
                "download", filename=e.name, total=e.total or None
            )
        elif e.kind == "file_progress":
            tid = self.tasks.get(e.key)
            if tid is not None:
                self.progress.update(tid, advance=e.advance)
        elif e.kind == "file_end":
            tid = self.tasks.pop(e.key, None)
            if tid is not None:
                self.progress.remove_task(tid)
            if not e.ok:
                console.print(f"[red]  failed:[/red] {e.name} ({e.message})")
        elif e.kind == "info" and e.message:
            console.print(f"[dim]{e.message}[/dim]")

    def close(self) -> None:
        if self.live:
            self.progress.stop()
            self.live = False


@app.callback()
def main_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose logging"),
):
    ctx.obj = {"verbose": verbose}


@app.command()
def help():
    """Show detailed help information about canvas-dl."""
    help_text = Text()
    help_text.append("canvas-dl - Canvas course downloader\n\n", style="bold blue")

    help_text.append("DESCRIPTION\n", style="bold")
    help_text.append(
        "Download files, all course files (with folder structure), and assignments "
        "(with instructions as PDF) from Canvas. Optionally merge lecture PDFs and "
        "zip the result. Use the CLI or the desktop GUI (`canvas-dl gui`).\n\n"
    )

    help_text.append("COMMANDS\n", style="bold")
    for name, desc in [
        ("auth", "Configure your Canvas access token"),
        ("courses", "List your Canvas courses"),
        ("download", "Download files / files-tree / assignments from a course"),
        ("gui", "Launch the desktop graphical interface"),
        ("version", "Show version information"),
        ("help", "Show this help message"),
    ]:
        help_text.append("• ", style="cyan")
        help_text.append(name, style="bold")
        help_text.append(f" - {desc}\n")

    help_text.append("\nEXAMPLES\n", style="bold")
    help_text.append("  canvas-dl auth\n", style="yellow")
    help_text.append("  canvas-dl courses --published\n", style="yellow")
    help_text.append("  canvas-dl download --course-id 45952            # everything\n", style="yellow")
    help_text.append("  canvas-dl download --content files --only pdf   # all PDFs, folder tree\n", style="yellow")
    help_text.append("  canvas-dl download --content assignments        # assignments + instructions\n", style="yellow")
    help_text.append("  canvas-dl download --merge --merge-scope both --zip\n", style="yellow")
    help_text.append("  canvas-dl gui\n", style="yellow")

    help_text.append("\nCONTENT SOURCES (--content)\n", style="bold")
    help_text.append("  modules       files referenced from course modules (Modules/<module>/)\n")
    help_text.append("  files         every course file, mirroring the Canvas folder tree (Files/)\n")
    help_text.append("  assignments   attachments + instructions.pdf per assignment (Assignments/)\n")
    help_text.append("  all           all of the above (default)\n")

    console.print(Panel(help_text, title="canvas-dl Help", border_style="blue"))


@app.command()
def version():
    """Show version."""
    console.print(f"canvas-dl {__version__}")


@app.command()
def gui():
    """Launch the desktop graphical interface."""
    from .gui import run

    raise typer.Exit(code=run())


@app.command()
def auth(api_url: str = typer.Option(DEFAULT_API_URL, help="Canvas API base URL")):
    """Prompt for token and save to config file."""
    token = questionary.password("Enter Canvas access token:").ask()
    if not token:
        raise typer.Exit(code=1)
    cfg = AppConfig.from_sources()
    cfg.api_url = api_url
    cfg.access_token = token
    cfg.save()
    console.print(f"Saved token to {AppConfig.config_path()}")


@app.command()
def courses(
    api_url: Optional[str] = typer.Option(None, help="Canvas API base URL override"),
    token: Optional[str] = typer.Option(None, help="Access token override"),
    published: bool = typer.Option(False, help="Only show published courses"),
):
    """List your courses."""
    cfg = AppConfig.from_sources()
    client = _build_client(cfg, api_url, token)

    dirs = get_app_dirs()
    cache_path = Path(dirs.user_cache_dir) / "courses.json"
    from .utils import TTLCache

    cache = TTLCache(cache_path, ttl_seconds=300)
    data = cache.load()
    if data is None:
        try:
            data = client.list_courses(published=published or None)
        except CanvasAPIError as e:
            console.print(f"[red]Error listing courses:[/red] {e}")
            raise typer.Exit(code=1)
        finally:
            client.close()
        cache.save(data)
    else:
        client.close()

    table = Table(title="Courses", box=box.SIMPLE_HEAVY)
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="white")
    table.add_column("Term", style="magenta")
    table.add_column("Published", style="green")
    for c in data:
        table.add_row(
            str(c.get("id")),
            c.get("name", ""),
            (c.get("term") or {}).get("name", ""),
            str(c.get("workflow_state") == "available"),
        )
    console.print(table)


@app.command()
def download(
    course_id: Optional[int] = typer.Option(None, help="Course ID to download"),
    content: str = typer.Option(
        "all", help="What to download: comma list of modules,files,assignments (or 'all')"
    ),
    api_url: Optional[str] = typer.Option(None, help="Canvas API base URL override"),
    token: Optional[str] = typer.Option(None, help="Access token override"),
    dest: Optional[Path] = typer.Option(None, help="Destination directory (default ./downloads)"),
    only: Optional[str] = typer.Option(None, help="Only these file types, comma-separated (pdf,ipynb)"),
    name: Optional[str] = typer.Option(None, help="Filter by name (glob, e.g. *lecture*)"),
    regex: Optional[str] = typer.Option(None, help="Filter by name (regex)"),
    concurrency: Optional[int] = typer.Option(None, help="Concurrent downloads"),
    no_instructions: bool = typer.Option(False, "--no-instructions", help="Skip assignment instruction PDFs"),
    merge: bool = typer.Option(False, "--merge", help="Merge lecture PDFs into combined PDFs"),
    merge_scope: str = typer.Option("per-module", help="Merge scope: per-module|course|both|tree"),
    zip_output: bool = typer.Option(False, "--zip", help="Zip the course folder when done"),
):
    """Download content for a course (files, full file tree, and/or assignments)."""
    cfg = AppConfig.from_sources()
    client = _build_client(cfg, api_url, token)

    if course_id is None:
        try:
            all_courses = client.list_courses(published=True)
        except CanvasAPIError as e:
            console.print(f"[red]Error listing courses:[/red] {e}")
            raise typer.Exit(code=1)
        if not all_courses:
            console.print("No courses found.")
            raise typer.Exit(code=1)
        choice = questionary.select(
            "Pick a course",
            choices=[
                questionary.Choice(title=f"{c.get('name')} ({c.get('id')})", value=c)
                for c in all_courses
            ],
        ).ask()
        if not choice:
            raise typer.Exit(code=1)
        course_id = int(choice["id"])
        course_name = choice.get("name") or f"course-{course_id}"
    else:
        try:
            course = next(c for c in client.list_courses() if int(c.get("id")) == int(course_id))
            course_name = course.get("name") or f"course-{course_id}"
        except Exception:
            course_name = f"course-{course_id}"

    dest_root = (dest or Path("downloads")).expanduser().resolve()
    course_dest = dest_root / _sanitize_course_dir(course_name)

    opts = DownloadOptions(
        sources=DownloadOptions.parse_sources(content),
        only_exts=[s.strip() for s in only.split(",")] if only else None,
        name_glob=name,
        name_regex=regex,
        concurrency=concurrency or cfg.concurrency,
        include_assignment_instructions=not no_instructions,
        merge_pdfs=merge,
        merge_scope=merge_scope,
        zip_output=zip_output,
    )

    console.print(f"Downloading [bold]{course_name}[/bold] → {course_dest}")
    console.print(f"Sources: [cyan]{', '.join(sorted(opts.sources))}[/cyan]")

    reporter = _CliReporter()
    try:
        result: DownloadResult = asyncio.run(
            download_course(client, int(course_id), course_name, course_dest, opts, reporter)
        )
    except CanvasAPIError as e:
        reporter.close()
        console.print(f"[red]Error during download:[/red] {e}")
        raise typer.Exit(code=1)
    finally:
        reporter.close()
        client.close()

    console.print(
        f"[green]✓[/green] {len(result.downloaded)} file(s) downloaded, "
        f"{result.skipped} unchanged, {len(result.failed)} failed."
    )
    if result.instructions:
        console.print(f"  {len(result.instructions)} assignment instruction file(s).")
    if result.merged:
        console.print(f"  Merged PDFs: {', '.join(p.name for p in result.merged)}")
    if result.zip_path:
        console.print(f"  Zip: {result.zip_path}")
    if result.failed:
        console.print(f"[yellow]Failed:[/yellow] {', '.join(result.failed[:10])}")


def _sanitize_course_dir(name: str) -> str:
    return sanitize_filename(name).rstrip(" .") or "course"


# Public alias kept for backwards compatibility.
sanitize_course_dir = _sanitize_course_dir


def main():  # entry point
    app()
