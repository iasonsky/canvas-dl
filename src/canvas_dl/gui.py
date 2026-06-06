"""Native desktop GUI for canvas-dl (customtkinter).

Launch with ``canvas-dl-gui`` or ``python -m canvas_dl.gui``. All Canvas/IO
logic lives in :mod:`canvas_dl.gui_controller`; this file is just the view.
Worker threads never touch widgets directly — they push events onto a queue
that the Tk main loop drains via ``after``.
"""

from __future__ import annotations

import queue
import sys
import traceback
from pathlib import Path
from typing import Dict, List, Optional

from . import __version__
from .download import DownloadResult, ProgressEvent
from .gui_controller import GuiController, build_options


def _humanize_phase(phase: str) -> str:
    return {
        "metadata": "Fetching course metadata…",
        "download": "Downloading files…",
        "instructions": "Rendering assignment instructions…",
        "merge": "Merging PDFs…",
        "zip": "Creating zip archive…",
        "done": "Done!",
    }.get(phase, phase)


def create_app():
    """Build (but do not run) the GUI. Imports Tk lazily so the module stays
    importable on headless machines. Raises if no display/Tk is available."""
    import customtkinter as ctk
    from tkinter import filedialog, messagebox

    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")

    class App(ctk.CTk):
        def __init__(self) -> None:
            super().__init__()
            self.controller = GuiController()
            self.events: "queue.Queue[object]" = queue.Queue()
            self.course_by_label: Dict[str, dict] = {}
            self.downloading = False
            self._total_files = 0
            self._done_files = 0

            self.title(f"Canvas Downloader v{__version__}")
            self.geometry("640x720")
            self.minsize(560, 640)
            self.grid_columnconfigure(0, weight=1)

            pad = {"padx": 16, "pady": (8, 0)}

            ctk.CTkLabel(
                self, text="Canvas Downloader",
                font=ctk.CTkFont(size=22, weight="bold"),
            ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 0))

            # --- token row ------------------------------------------------ #
            token_frame = ctk.CTkFrame(self)
            token_frame.grid(row=1, column=0, sticky="ew", **pad)
            token_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(token_frame, text="Token").grid(row=0, column=0, padx=(10, 8), pady=10)
            self.token_entry = ctk.CTkEntry(token_frame, show="•", placeholder_text="Canvas access token")
            self.token_entry.grid(row=0, column=1, sticky="ew", pady=10)
            ctk.CTkButton(token_frame, text="Save", width=70, command=self.on_save_token).grid(
                row=0, column=2, padx=10, pady=10
            )
            ctk.CTkLabel(token_frame, text="API URL").grid(row=1, column=0, padx=(10, 8), pady=(0, 10))
            self.api_entry = ctk.CTkEntry(token_frame)
            self.api_entry.insert(0, self.controller.api_url)
            self.api_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 10), pady=(0, 10))

            # --- course row ----------------------------------------------- #
            course_frame = ctk.CTkFrame(self)
            course_frame.grid(row=2, column=0, sticky="ew", **pad)
            course_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(course_frame, text="Course").grid(row=0, column=0, padx=(10, 8), pady=10)
            self.course_menu = ctk.CTkOptionMenu(course_frame, values=["(load courses)"])
            self.course_menu.grid(row=0, column=1, sticky="ew", pady=10)
            ctk.CTkButton(course_frame, text="Refresh", width=80, command=self.on_refresh).grid(
                row=0, column=2, padx=10, pady=10
            )

            # --- sources + filters ---------------------------------------- #
            opt_frame = ctk.CTkFrame(self)
            opt_frame.grid(row=3, column=0, sticky="ew", **pad)
            opt_frame.grid_columnconfigure((0, 1, 2), weight=1)
            ctk.CTkLabel(opt_frame, text="What to download").grid(
                row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0)
            )
            self.src_modules = ctk.CTkCheckBox(opt_frame, text="Modules")
            self.src_files = ctk.CTkCheckBox(opt_frame, text="All files")
            self.src_assign = ctk.CTkCheckBox(opt_frame, text="Assignments")
            for w in (self.src_modules, self.src_files, self.src_assign):
                w.select()
            self.src_modules.grid(row=1, column=0, sticky="w", padx=10, pady=6)
            self.src_files.grid(row=1, column=1, sticky="w", padx=10, pady=6)
            self.src_assign.grid(row=1, column=2, sticky="w", padx=10, pady=6)

            ctk.CTkLabel(opt_frame, text="Only types (e.g. pdf,ipynb)").grid(
                row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 0)
            )
            self.only_entry = ctk.CTkEntry(opt_frame, placeholder_text="leave blank for all")
            self.only_entry.grid(row=3, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 6))
            self.name_entry = ctk.CTkEntry(opt_frame, placeholder_text="name filter, e.g. *lecture*")
            self.name_entry.grid(row=3, column=2, sticky="ew", padx=10, pady=(0, 6))

            # --- post-processing options ---------------------------------- #
            self.merge_var = ctk.CTkCheckBox(opt_frame, text="Merge PDFs")
            self.merge_var.grid(row=4, column=0, sticky="w", padx=10, pady=6)
            self.merge_scope = ctk.CTkOptionMenu(opt_frame, values=["per-module", "course", "both"], width=120)
            self.merge_scope.grid(row=4, column=1, sticky="w", padx=10, pady=6)
            self.zip_var = ctk.CTkCheckBox(opt_frame, text="Zip output")
            self.zip_var.grid(row=4, column=2, sticky="w", padx=10, pady=6)
            self.instr_var = ctk.CTkCheckBox(opt_frame, text="Assignment instructions as PDF")
            self.instr_var.select()
            self.instr_var.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 10))

            # --- destination ---------------------------------------------- #
            dest_frame = ctk.CTkFrame(self)
            dest_frame.grid(row=4, column=0, sticky="ew", **pad)
            dest_frame.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(dest_frame, text="Save to").grid(row=0, column=0, padx=(10, 8), pady=10)
            self.dest_entry = ctk.CTkEntry(dest_frame)
            self.dest_entry.insert(0, str(Path.home() / "Downloads" / "Canvas"))
            self.dest_entry.grid(row=0, column=1, sticky="ew", pady=10)
            ctk.CTkButton(dest_frame, text="Browse", width=80, command=self.on_browse).grid(
                row=0, column=2, padx=10, pady=10
            )

            # --- download + progress -------------------------------------- #
            self.download_btn = ctk.CTkButton(
                self, text="Download", height=40,
                font=ctk.CTkFont(size=16, weight="bold"), command=self.on_download,
            )
            self.download_btn.grid(row=5, column=0, sticky="ew", padx=16, pady=(12, 4))

            self.progress = ctk.CTkProgressBar(self)
            self.progress.set(0)
            self.progress.grid(row=6, column=0, sticky="ew", padx=16, pady=(8, 4))
            self.status = ctk.CTkLabel(self, text="Ready", anchor="w")
            self.status.grid(row=7, column=0, sticky="ew", padx=16)

            self.log = ctk.CTkTextbox(self, height=160)
            self.log.grid(row=8, column=0, sticky="nsew", padx=16, pady=(8, 16))
            self.grid_rowconfigure(8, weight=1)

            if self.controller.has_token():
                self.token_entry.insert(0, "•" * 12)
                self.on_refresh()
            else:
                self._log("Enter your Canvas token and click Save to begin.")

            self.protocol("WM_DELETE_WINDOW", self._on_close)
            self.after(100, self._poll)

        # -- helpers ------------------------------------------------------- #
        def _on_close(self) -> None:
            try:
                self.controller.close()
            finally:
                self.destroy()
        def _log(self, msg: str) -> None:
            self.log.insert("end", msg + "\n")
            self.log.see("end")

        def _set_status(self, msg: str) -> None:
            self.status.configure(text=msg)

        # -- actions ------------------------------------------------------- #
        def on_save_token(self) -> None:
            token = self.token_entry.get().strip()
            if not token or set(token) == {"•"}:
                messagebox.showwarning("Token", "Please paste your Canvas access token.")
                return
            self.controller.save_token(token, self.api_entry.get().strip())
            self.token_entry.delete(0, "end")
            self.token_entry.insert(0, "•" * 12)
            self._log("Token saved.")
            self.on_refresh()

        def on_refresh(self) -> None:
            if not self.controller.has_token():
                messagebox.showwarning("Token", "Save a token first.")
                return
            self._set_status("Loading courses…")
            import threading

            def work() -> None:
                try:
                    courses = self.controller.load_courses(force=True)
                    self.events.put(("courses", courses))
                except Exception as exc:  # noqa: BLE001
                    self.events.put(("error", exc))

            threading.Thread(target=work, daemon=True).start()

        def on_browse(self) -> None:
            chosen = filedialog.askdirectory(initialdir=self.dest_entry.get() or str(Path.home()))
            if chosen:
                self.dest_entry.delete(0, "end")
                self.dest_entry.insert(0, chosen)

        def _selected_sources(self) -> List[str]:
            out = []
            if self.src_modules.get():
                out.append("modules")
            if self.src_files.get():
                out.append("files")
            if self.src_assign.get():
                out.append("assignments")
            return out

        def on_download(self) -> None:
            if self.downloading:
                return
            label = self.course_menu.get()
            course = self.course_by_label.get(label)
            if not course:
                messagebox.showwarning("Course", "Pick a course first (Refresh to load).")
                return
            sources = self._selected_sources()
            if not sources:
                messagebox.showwarning("Sources", "Select at least one of Modules / Files / Assignments.")
                return
            opts = build_options(
                sources=sources,
                only=self.only_entry.get().strip() or None,
                name_glob=self.name_entry.get().strip() or None,
                instructions=bool(self.instr_var.get()),
                merge=bool(self.merge_var.get()),
                merge_scope=self.merge_scope.get(),
                zip_output=bool(self.zip_var.get()),
            )
            self.downloading = True
            self.download_btn.configure(state="disabled", text="Downloading…")
            self.progress.set(0)
            self._done_files = 0
            self._total_files = 0
            self._log(f"Starting download: {course.get('name')}")

            self.controller.start_download(
                course=course,
                dest_root=Path(self.dest_entry.get().strip() or "."),
                opts=opts,
                on_event=lambda e: self.events.put(("event", e)),
                on_done=lambda r: self.events.put(("done", r)),
                on_error=lambda exc: self.events.put(("error", exc)),
            )

        # -- queue pump (runs on the Tk main thread) ----------------------- #
        def _poll(self) -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "courses":
                        self._on_courses(payload)
                    elif kind == "event":
                        self._on_event(payload)
                    elif kind == "done":
                        self._on_done(payload)
                    elif kind == "error":
                        self._on_error(payload)
            except queue.Empty:
                pass
            self.after(100, self._poll)

        def _on_courses(self, courses: List[dict]) -> None:
            self.course_by_label = {
                f"{c.get('name')} ({c.get('id')})": c for c in courses if c.get("id")
            }
            labels = list(self.course_by_label) or ["(no courses found)"]
            self.course_menu.configure(values=labels)
            self.course_menu.set(labels[0])
            self._set_status(f"Loaded {len(self.course_by_label)} course(s).")

        def _on_event(self, e: ProgressEvent) -> None:
            if e.kind == "phase":
                self._set_status(_humanize_phase(e.phase))
                if e.phase == "download":
                    self._total_files = e.total
                    self._done_files = 0
                if e.message:
                    self._log(e.message)
            elif e.kind == "file_end":
                self._done_files += 1
                if self._total_files:
                    self.progress.set(self._done_files / self._total_files)
                self._set_status(f"{self._done_files}/{self._total_files}  {e.name}")
                if not e.ok:
                    self._log(f"  failed: {e.name} ({e.message})")
            elif e.kind == "info" and e.message:
                self._log(e.message)

        def _on_done(self, result: DownloadResult) -> None:
            self.downloading = False
            self.download_btn.configure(state="normal", text="Download")
            self.progress.set(1)
            summary = (
                f"Done. {len(result.downloaded)} file(s), {result.skipped} unchanged, "
                f"{len(result.failed)} failed."
            )
            self._set_status(summary)
            self._log(summary)
            self._log(f"Saved to: {result.dest_dir}")
            if result.merged:
                self._log(f"Merged {len(result.merged)} PDF(s).")
            if result.zip_path:
                self._log(f"Zip: {result.zip_path}")
            messagebox.showinfo("Canvas Downloader", summary + f"\n\nSaved to:\n{result.dest_dir}")

        def _on_error(self, exc: Exception) -> None:
            self.downloading = False
            self.download_btn.configure(state="normal", text="Download")
            self._set_status("Error.")
            self._log("ERROR: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
            messagebox.showerror("Canvas Downloader", str(exc))

    return App()


def run() -> int:
    try:
        app = create_app()
    except Exception as exc:  # pragma: no cover - environment without a display/tk
        sys.stderr.write(
            "Could not start the GUI (is a display / Tk available?).\n"
            f"Error: {exc}\n"
            "You can still use the command line: canvas-dl --help\n"
        )
        return 1
    app.mainloop()
    return 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
