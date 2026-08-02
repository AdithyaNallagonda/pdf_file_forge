"""Tkinter front-end. Deliberately plain; all the work happens in app/ops."""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, W, X, Tk, filedialog, messagebox, ttk
import tkinter as tk

from . import batch, config, ocr, registry
from .workspace import Cancelled, OpContext, human, purge_temp_root, session_temp


class App(tk.Frame):
    def __init__(self, master: Tk, initial: list[Path] | None = None,
                 auto_op: str | None = None, fallback_reason: str = ""):
        super().__init__(master, padx=12, pady=10)
        self.pack(fill=BOTH, expand=True)
        self.files: list[Path] = []
        self.op_buttons: list[ttk.Button] = []
        self.results: list[Path] = []
        self.events: queue.Queue = queue.Queue()
        self.cancel_flag = threading.Event()
        self.worker: threading.Thread | None = None
        self._build()
        if fallback_reason:
            self.status.config(text="Classic fallback interface active.", foreground="#a33")
            self._write("NOTICE: " + fallback_reason)
        self.after(100, self._drain)
        if initial:
            self.files = [p for p in initial if p.exists()]
            self._refresh_actions()
            if auto_op:
                op = next((o for o in registry.operations_for(self.files)
                           if o.id == auto_op), None)
                if op:
                    self.after(400, lambda: self.start(op))

    # ---------------- layout ----------------
    def _build(self) -> None:
        r = self.master
        r.title(f"{config.APP_NAME} — local document toolkit")
        r.geometry("760x560")
        r.minsize(680, 500)

        # 1. input
        box = ttk.LabelFrame(self, text="1 · Choose file(s)", padding=8)
        box.pack(fill=X)
        self.file_lbl = ttk.Label(box, text="No file selected.", foreground="#555")
        self.file_lbl.pack(side=LEFT, fill=X, expand=True, anchor=W)
        ttk.Button(box, text="Browse files…", command=self.pick).pack(side=RIGHT)
        ttk.Button(box, text="Add folder…", command=self.pick_folder).pack(side=RIGHT, padx=(0, 6))
        subrow = ttk.Frame(self)
        subrow.pack(fill=X, pady=(3, 0))
        self.recursive = tk.BooleanVar(value=True)
        ttk.Checkbutton(subrow, text="Include sub-folders",
                        variable=self.recursive).pack(side=LEFT)
        ttk.Label(subrow, text="Files at once:").pack(side=LEFT, padx=(16, 4))
        self.workers = tk.IntVar(value=batch.default_workers())
        ttk.Spinbox(subrow, from_=1, to=16, width=4,
                    textvariable=self.workers).pack(side=LEFT)
        ttk.Button(subrow, text="Clear", command=self.clear_files).pack(side=RIGHT)
        ttk.Label(subrow, foreground="#777", text="  (drag files onto the desktop "
                  "icon to open them here)").pack(side=RIGHT)

        # 2. dynamic actions
        self.actions = ttk.LabelFrame(self, text="2 · Choose an action", padding=8)
        self.actions.pack(fill=X, pady=(10, 0))
        self.hint = ttk.Label(self.actions, foreground="#777",
                              text="Actions appear once a supported file is selected.")
        self.hint.pack(anchor=W)

        # options
        opt = ttk.Frame(self)
        opt.pack(fill=X, pady=(8, 0))
        ttk.Label(opt, text="Compression target (MB):").pack(side=LEFT)
        self.target = tk.IntVar(value=config.DEFAULT_TARGET_MB)
        ttk.Spinbox(opt, from_=1, to=5000, increment=25, width=7,
                    textvariable=self.target).pack(side=LEFT, padx=(6, 18))
        ttk.Label(opt, text="Split Markdown every (MB):").pack(side=LEFT)
        self.split = tk.DoubleVar(value=config.DEFAULT_SPLIT_MB)
        ttk.Spinbox(opt, from_=0, to=50, increment=1, width=5,
                    textvariable=self.split).pack(side=LEFT, padx=(6, 18))
        ttk.Label(opt, text="Image DPI:").pack(side=LEFT)
        self.dpi = tk.IntVar(value=200)
        ttk.Spinbox(opt, from_=72, to=600, increment=25, width=6,
                    textvariable=self.dpi).pack(side=LEFT, padx=6)

        # 3. progress + log
        fig = ttk.Frame(self)
        fig.pack(fill=X, pady=(6, 0))
        self.describe_figures = tk.BooleanVar(value=True)
        self.mermaid = tk.BooleanVar(value=True)
        self.export_figures = tk.BooleanVar(value=False)
        ttk.Checkbutton(fig, text="Describe drawings & charts inline",
                        variable=self.describe_figures).pack(side=LEFT)
        ttk.Checkbutton(fig, text="Rebuild flowcharts as diagrams",
                        variable=self.mermaid).pack(side=LEFT, padx=14)
        ttk.Checkbutton(fig, text="Also save figure pages as images",
                        variable=self.export_figures).pack(side=LEFT)

        lang = ttk.Frame(self)
        lang.pack(fill=X, pady=(6, 0))
        self.langs = tk.StringVar(value=ocr.DEFAULT_LANGS)
        self._available_langs = ocr.list_languages(config.find_tesseract())
        ttk.Label(lang, text="Document languages:").pack(side=LEFT)
        self.lang_lbl = ttk.Label(lang, foreground="#036", text="")
        self.lang_lbl.pack(side=LEFT, padx=6)
        ttk.Button(lang, text="Choose…", command=self.pick_langs).pack(side=LEFT)
        self.force_ocr = tk.BooleanVar(value=False)
        self.auto_langs = tk.BooleanVar(value=True)
        ttk.Checkbutton(lang, text="Auto-narrow to scripts found",
                        variable=self.auto_langs).pack(side=LEFT, padx=14)
        ttk.Checkbutton(lang, text="Force OCR (ignore the text layer)",
                        variable=self.force_ocr).pack(side=LEFT)
        self._sync_langs()

        run = ttk.LabelFrame(self, text="3 · Progress", padding=8)
        run.pack(fill=BOTH, expand=True, pady=(10, 0))
        self.bar = ttk.Progressbar(run, mode="determinate", maximum=100)
        self.bar.pack(fill=X)
        self.status = ttk.Label(run, text="Idle.", foreground="#333")
        self.status.pack(anchor=W, pady=(4, 6))
        self.log = tk.Text(run, height=11, wrap="word", state="disabled",
                           font=("Consolas", 9), bg="#101418", fg="#d6dde6")
        self.log.pack(fill=BOTH, expand=True)

        foot = ttk.Frame(self)
        foot.pack(fill=X, pady=(8, 0))
        self.cancel_btn = ttk.Button(foot, text="Cancel", state="disabled",
                                     command=self.cancel)
        self.cancel_btn.pack(side=LEFT)
        ttk.Button(foot, text="Open results folder",
                   command=lambda: self._reveal(config.RESULT_DIR)).pack(side=RIGHT)
        ttk.Label(foot, text=f"Workspace: {config.BASE_DIR}",
                  foreground="#888").pack(side=RIGHT, padx=10)

        self._write(f"Workspace  : {config.BASE_DIR}")
        self._write(f"Ghostscript: {config.find_ghostscript() or 'NOT FOUND (compression falls back to raster)'}")
        tess = config.find_tesseract() or "not found (scanned pages will not be OCRed)"
        self._write(f"Tesseract  : {tess}")
        self._write("Languages  : " + (", ".join(self._available_langs)
                                        if self._available_langs else "none installed"))

    # ---------------- selection ----------------
    def pick(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        chosen = filedialog.askopenfilenames(
            title="Select file(s)", filetypes=registry.FILE_TYPES + [("All files", "*.*")])
        if not chosen:
            return
        self.files = [Path(c) for c in chosen]
        groups = {registry.group_key(p.suffix.lower()) for p in self.files}
        if len(groups) > 1:
            messagebox.showwarning(config.APP_NAME, "Please select files of one type only.")
            self.files = []
        self._refresh_actions()

    def _sync_langs(self) -> None:
        codes = [c for c in self.langs.get().split("+") if c]
        pretty = ", ".join(ocr.LANG_NAMES.get(c, c).split(" (")[0] for c in codes)
        self.lang_lbl.config(text=pretty or "English")

    def pick_langs(self) -> None:
        """Only offers packs that are actually installed."""
        avail = self._available_langs
        if not avail:
            messagebox.showinfo(config.APP_NAME,
                "No OCR engine was found, so no language packs are available.\n\n"
                "Scanned documents cannot be read until Tesseract is installed.")
            return
        win = tk.Toplevel(self)
        win.title("Document languages")
        win.transient(self.master)
        win.grab_set()
        ttk.Label(win, padding=8, wraplength=380, text=
                  "Tick the languages that appear in the documents. Two or three "
                  "is best — each extra language slows reading and can reduce "
                  "accuracy on the others.").pack(anchor=W)

        chosen = set(self.langs.get().split("+"))
        vars_: dict[str, tk.BooleanVar] = {}
        body = ttk.Frame(win, padding=(12, 0))
        body.pack(fill=BOTH, expand=True)
        ordered = ([c for c in ocr.LANG_NAMES if c in avail and c != "osd"]
                   + [c for c in avail if c not in ocr.LANG_NAMES])
        for n, code in enumerate(ordered):
            v = tk.BooleanVar(value=code in chosen)
            vars_[code] = v
            ttk.Checkbutton(body, text=ocr.LANG_NAMES.get(code, code),
                            variable=v).grid(row=n % 12, column=n // 12,
                                             sticky=W, padx=6, pady=1)

        def apply() -> None:
            picked = [c for c, v in vars_.items() if v.get()]
            self.langs.set("+".join(picked) if picked else "eng")
            self._sync_langs()
            win.destroy()

        bar = ttk.Frame(win, padding=8)
        bar.pack(fill=X)
        ttk.Button(bar, text="OK", command=apply).pack(side=RIGHT)
        ttk.Button(bar, text="Cancel", command=win.destroy).pack(side=RIGHT, padx=6)

    def pick_folder(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        folder = filedialog.askdirectory(title="Select a folder of documents")
        if not folder:
            return
        self.status.config(text="Scanning folder…")
        self.update_idletasks()
        exts = {e for op in registry.OPERATIONS for e in op.extensions}
        found = batch.collect_files([Path(folder)], exts, self.recursive.get())
        if not found:
            messagebox.showinfo(config.APP_NAME, "No supported documents found there.")
            self.status.config(text="Idle.")
            return

        groups: dict[str, list[Path]] = {}
        for f in found:
            groups.setdefault(registry.group_key(f.suffix.lower()), []).append(f)
        if len(groups) > 1:
            key = max(groups, key=lambda k: len(groups[k]))
            skipped = sum(len(v) for k, v in groups.items() if k != key)
            self._write(f"Folder holds mixed types; taking {len(groups[key])} "
                        f"{key} file(s), ignoring {skipped} other file(s).")
            found = groups[key]

        self.files = found
        self._write(f"Added {len(found)} file(s) from {folder}")
        self._refresh_actions()

    def clear_files(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.files = []
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        for w in self.actions.winfo_children():
            w.destroy()
        if not self.files:
            self.file_lbl.config(text="No file selected.")
            ttk.Label(self.actions, foreground="#777",
                      text="Actions appear once a supported file is selected.").pack(anchor=W)
            return

        total = sum(p.stat().st_size for p in self.files)
        name = (self.files[0].name if len(self.files) == 1
                else f"{len(self.files)} files ({self.files[0].suffix.lower()})")
        self.file_lbl.config(text=f"{name}  ({human(total)})", foreground="#000")

        ops = registry.operations_for(self.files)
        if not ops:
            ttk.Label(self.actions, foreground="#a33",
                      text="No action available for this file type.").pack(anchor=W)
            return
        grid = ttk.Frame(self.actions)
        grid.pack(fill=X)
        self.op_buttons = []
        for i, op in enumerate(ops):
            cell = ttk.Frame(grid, padding=(0, 3))
            cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 12))
            grid.columnconfigure(i % 2, weight=1)
            text = f"★  {op.label}" if op.primary else op.label
            btn = ttk.Button(cell, text=text, width=24,
                             command=lambda o=op: self.start(o))
            btn.pack(side=LEFT)
            self.op_buttons.append(btn)
            ttk.Label(cell, text=op.description, foreground="#666",
                      wraplength=210).pack(side=LEFT, padx=6)

    # ---------------- execution ----------------
    def start(self, op: registry.Operation) -> None:
        if self.worker and self.worker.is_alive():
            return
        files = list(self.files)
        opts = {"target_mb": self.target.get(), "image_dpi": self.dpi.get(),
                "split_mb": self.split.get(), "ocr_dpi": max(300, self.dpi.get()),
                "describe_figures": self.describe_figures.get(),
                "mermaid": self.mermaid.get(),
                "export_figures": self.export_figures.get(),
                "ocr_lang": self.langs.get(), "force_ocr": self.force_ocr.get(),
                "auto_langs": self.auto_langs.get()}
        workers = max(1, int(self.workers.get()))
        # One file => spend the cores inside it (parallel OCR).
        # Many files => spend them across files; nesting pools only thrashes.
        opts["ocr_workers"] = workers if len(files) == 1 else 1
        if not op.parallel_safe and workers > 1 and len(files) > 1:
            self._write("This action drives Microsoft Word, which cannot be run "
                        "several times at once — processing sequentially.")
        self.cancel_flag.clear()
        self.bar["value"] = 0
        self._set_busy(True)
        self._write(f"\n=== {op.label} — {datetime.now():%H:%M:%S} ===")
        self.worker = threading.Thread(target=self._run, args=(op, files, opts, workers),
                                       daemon=True)
        self.worker.start()

    def _run(self, op: registry.Operation, files: list[Path], opts: dict,
             workers: int = 1) -> None:
        try:
            # Many files + a per-file operation => run them concurrently.
            if op.batch and len(files) > 1 and workers > 1 and op.parallel_safe:
                self.events.put(("progress", 0,
                                 f"Processing {len(files)} files, {workers} at a time…"))
                outs, fails = batch.run_parallel(
                    op.id, files, opts, workers,
                    lambda p, m: self.events.put(("progress", p, m)),
                    self.cancel_flag.is_set)
                if self.cancel_flag.is_set():
                    self.events.put(("cancelled", "", ""))
                elif not outs:
                    raise RuntimeError("All files failed:\n" + "\n".join(fails[:5]))
                else:
                    self.events.put(("done_many", outs, fails))
                return
            with session_temp() as tmp:
                emit = lambda p, m: self.events.put(("progress", p, m))  # noqa: E731
                ctx = OpContext(tmp, emit, self.cancel_flag.is_set, opts)
                if op.batch and len(files) > 1:
                    outputs, failed = [], []
                    for i, f in enumerate(files):
                        ctx.check_cancel()
                        lo, hi = 100 * i / len(files), 100 * (i + 1) / len(files)
                        sub = ctx.scoped(lo, hi, f"[{i + 1}/{len(files)}] ")
                        sub.log(f"--- {f.name}")
                        try:
                            outputs.append(op.handler([f], sub))
                        except Cancelled:
                            raise
                        except Exception as e:
                            failed.append(f"{f.name}: {type(e).__name__}: {e}")
                            sub.log(f"    SKIPPED — {e}")
                    if not outputs:
                        raise RuntimeError("All files failed:\n" + "\n".join(failed[:5]))
                    self.events.put(("done_many", [str(o) for o in outputs], failed))
                else:
                    result = op.handler(files, ctx)
                    self.events.put(("done", str(result), ""))
        except Cancelled:
            self.events.put(("cancelled", "", ""))
        except Exception as e:
            self.events.put(("error", f"{type(e).__name__}: {e}",
                             traceback.format_exc()))

    def cancel(self) -> None:
        self.cancel_flag.set()
        self.status.config(text="Cancelling…")

    # ---------------- event pump ----------------
    def _drain(self) -> None:
        try:
            while True:
                kind, a, b = self.events.get_nowait()
                if kind == "progress":
                    if a >= 0:
                        self.bar["value"] = a
                    if b:
                        self.status.config(text=b)
                        self._write(f"  {b}")
                elif kind == "done":
                    self.bar["value"] = 100
                    self.status.config(text="Completed.")
                    self._write(f"OUTPUT: {a}")
                    self._set_busy(False)
                    if messagebox.askyesno(config.APP_NAME,
                                           f"Completed.\n\n{a}\n\nOpen the results folder?"):
                        self._reveal(Path(a))
                elif kind == "done_many":
                    self.bar["value"] = 100
                    for o in a:
                        self._write(f"OUTPUT: {o}")
                    note = f"Completed {len(a)} file(s)."
                    if b:
                        note += f" {len(b)} failed."
                        for f in b:
                            self._write(f"FAILED: {f}")
                    self.status.config(text=note)
                    self._set_busy(False)
                    if messagebox.askyesno(config.APP_NAME, note + "\n\nOpen the results folder?"):
                        self._reveal(config.RESULT_DIR)
                elif kind == "cancelled":
                    self.status.config(text="Cancelled — temporary files removed.")
                    self._write("CANCELLED")
                    self._set_busy(False)
                elif kind == "error":
                    self.status.config(text="Failed.")
                    self._write(f"ERROR: {a}")
                    self._write(b.strip().splitlines()[-1] if b else "")
                    self._set_busy(False)
                    messagebox.showerror(config.APP_NAME, a)
        except queue.Empty:
            pass
        self.after(120, self._drain)

    # ---------------- helpers ----------------
    def _set_busy(self, busy: bool) -> None:
        self.cancel_btn.config(state="normal" if busy else "disabled")
        for b in self.op_buttons:
            try:
                b.config(state="disabled" if busy else "normal")
            except tk.TclError:
                pass

    def _write(self, msg: str) -> None:
        self.log.config(state="normal")
        self.log.insert(END, msg + "\n")
        self.log.see(END)
        self.log.config(state="disabled")

    @staticmethod
    def _reveal(path: Path) -> None:
        target = path if path.is_dir() else path.parent
        try:
            if sys.platform == "win32":
                if path.is_file():
                    subprocess.Popen(["explorer", "/select,", str(path)])
                else:
                    os.startfile(target)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(target)])
        except Exception:
            pass


def main(argv: list[str] | None = None, fallback_reason: str = "") -> None:
    """argv: file paths (drag-and-drop onto the icon) plus optional --op <id>."""
    purge_temp_root()          # clear anything a previous crash left behind
    args = list(argv if argv is not None else sys.argv[1:])
    auto_op = None
    if "--op" in args:
        i = args.index("--op")
        auto_op = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    initial = [Path(a) for a in args if not a.startswith("-")]

    root = Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    app = App(root, initial=initial, auto_op=auto_op, fallback_reason=fallback_reason)

    def on_close() -> None:
        app.cancel_flag.set()
        purge_temp_root()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
