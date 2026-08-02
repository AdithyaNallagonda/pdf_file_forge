"""Flet front-end (Apache-2.0).

Layout follows the actual usage pattern: one prominent action, everything else
demoted. The primary user converts PDFs to Markdown all day and touches the
other settings a few times a year, so Advanced is collapsed by default and
remembers what he last set.

Flet's API has moved between releases (ft.icons -> ft.Icons, page.window_width
-> page.window.width). The shims below keep this working across both rather
than pinning users to one version.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import flet as ft

from . import batch, config, ocr, registry, settings as prefs
from .progress import Estimator
from .workspace import Cancelled, OpContext, human, purge_temp_root, session_temp

# ---------------------------------------------------------------- compat
_ICONS = getattr(ft, "Icons", None) or getattr(ft, "icons")
_COLORS = getattr(ft, "Colors", None) or getattr(ft, "colors")


def _icon(name: str, fallback: str = "CIRCLE"):
    return getattr(_ICONS, name, getattr(_ICONS, fallback, None))


def _colour(name: str, fallback: str = "BLUE"):
    return getattr(_COLORS, name, getattr(_COLORS, fallback, None))


def _set_window(page: ft.Page, **kw) -> None:
    win = getattr(page, "window", None)
    for key, value in kw.items():
        if win is not None and hasattr(win, key):
            setattr(win, key, value)
        elif hasattr(page, f"window_{key}"):
            setattr(page, f"window_{key}", value)


STATUS_ICON = {
    "waiting": ("SCHEDULE", "GREY"),
    "running": ("AUTORENEW", "BLUE"),
    "done": ("CHECK_CIRCLE", "GREEN"),
    "failed": ("ERROR", "RED"),
    "skipped": ("REMOVE_CIRCLE", "ORANGE"),
}


class FileRow(ft.Row):
    """One line of the status table. Updated in place, never rebuilt."""

    def __init__(self, path: Path):
        self.path = path
        self.icon = ft.Icon(_icon("SCHEDULE"), color=_colour("GREY"), size=18)
        self.name = ft.Text(path.name, size=13, expand=True,
                            overflow=ft.TextOverflow.ELLIPSIS, tooltip=str(path))
        self.detail = ft.Text(human(path.stat().st_size) if path.exists() else "",
                              size=12, color=_colour("GREY"), width=180,
                              overflow=ft.TextOverflow.ELLIPSIS)
        super().__init__(controls=[self.icon, self.name, self.detail],
                         spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def set(self, status: str, detail: str = "") -> None:
        name, colour = STATUS_ICON.get(status, ("CIRCLE", "GREY"))
        self.icon.name = _icon(name)
        self.icon.color = _colour(colour)
        if detail:
            self.detail.value = detail
            self.detail.color = _colour("RED") if status == "failed" else _colour("GREY")


class App:
    def __init__(self, page: ft.Page, initial: list[Path], auto_op: str | None):
        self.page = page
        self.cfg = prefs.load()
        self.files: list[Path] = []
        self.rows: dict[str, FileRow] = {}
        self.cancel = threading.Event()
        self.worker: threading.Thread | None = None
        self.est: Estimator | None = None
        self._last_paint = 0.0
        self._available_langs = ocr.list_languages(config.find_tesseract())

        self._build()
        if initial:
            self._set_files([p for p in initial if p.exists()])
            if auto_op:
                op = next((o for o in registry.operations_for(self.files)
                           if o.id == auto_op), None)
                if op:
                    self._start(op)

    # -------------------------------------------------------------- layout
    def _build(self) -> None:
        page = self.page
        page.title = f"{config.APP_NAME} — document toolkit"
        page.padding = 0
        page.theme_mode = ft.ThemeMode.LIGHT
        _set_window(page, width=940, height=760, min_width=780, min_height=620)

        self.picker = ft.FilePicker(on_result=self._on_files)
        self.folder_picker = ft.FilePicker(on_result=self._on_folder)
        page.overlay.extend([self.picker, self.folder_picker])

        header = ft.Container(
            bgcolor=_colour("BLUE_GREY_900"), padding=ft.padding.symmetric(24, 18),
            content=ft.Column(spacing=2, controls=[
                ft.Text(config.APP_NAME, size=22, weight=ft.FontWeight.BOLD,
                        color=_colour("WHITE")),
                ft.Text("Convert documents on this computer. Nothing is uploaded.",
                        size=13, color=_colour("BLUE_GREY_200"))]))

        self.drop_label = ft.Text("No documents chosen yet.", size=15)
        self.drop_sub = ft.Text("Choose files, or a folder to include everything "
                                "inside it.", size=12, color=_colour("GREY"))
        chooser = ft.Container(
            padding=18, border_radius=10, bgcolor=_colour("BLUE_GREY_50"),
            border=ft.border.all(1, _colour("BLUE_GREY_100")),
            content=ft.Row(spacing=14, controls=[
                ft.Icon(_icon("FOLDER_OPEN"), size=34, color=_colour("BLUE_GREY_400")),
                ft.Column(spacing=1, expand=True,
                          controls=[self.drop_label, self.drop_sub]),
                ft.FilledButton("Choose files", icon=_icon("DESCRIPTION"),
                                on_click=lambda _: self.picker.pick_files(
                                    allow_multiple=True)),
                ft.OutlinedButton("Choose folder", icon=_icon("FOLDER"),
                                  on_click=lambda _: self.folder_picker
                                  .get_directory_path()),
            ]))

        self.primary = ft.FilledButton(
            "Convert to Markdown", icon=_icon("AUTO_AWESOME"), disabled=True,
            height=52, style=ft.ButtonStyle(text_style=ft.TextStyle(size=16)),
            on_click=lambda _: self._start_by_id("pdf_to_md"))
        self.secondary = ft.Row(spacing=8, wrap=True)
        actions = ft.Column(spacing=10, controls=[self.primary, self.secondary])

        self.advanced = ft.ExpansionTile(
            title=ft.Text("Advanced settings"), initially_expanded=False,
            controls=[ft.Container(padding=ft.padding.only(12, 4, 12, 12),
                                   content=self._advanced_panel())])

        self.status = ft.Text("Ready.", size=14, weight=ft.FontWeight.W_500)
        self.eta = ft.Text("", size=13, color=_colour("GREY"))
        self.bar = ft.ProgressBar(value=0, height=8, border_radius=4)
        self.cancel_btn = ft.OutlinedButton("Cancel", icon=_icon("STOP_CIRCLE"),
                                            visible=False, on_click=self._cancel)
        self.results_btn = ft.TextButton("Open results folder",
                                         icon=_icon("FOLDER_OPEN"),
                                         on_click=lambda _: self._reveal())
        self.listview = ft.ListView(expand=True, spacing=4, padding=8, auto_scroll=False)
        self.counts = ft.Text("", size=12, color=_colour("GREY"))

        body = ft.Container(
            padding=ft.padding.symmetric(24, 18), expand=True,
            content=ft.Column(spacing=14, expand=True, controls=[
                chooser, actions, self.advanced,
                ft.Divider(height=1),
                ft.Row([self.status, ft.Container(expand=True), self.eta,
                        self.cancel_btn]),
                self.bar,
                ft.Container(expand=True, border_radius=8,
                             border=ft.border.all(1, _colour("BLUE_GREY_100")),
                             content=self.listview),
                ft.Row([self.counts, ft.Container(expand=True), self.results_btn]),
            ]))
        page.add(ft.Column(spacing=0, expand=True, controls=[header, body]))
        page.on_close = lambda _: self._save()

    def _advanced_panel(self) -> ft.Control:
        c = self.cfg

        def num(label, key, minimum, maximum, step=1, suffix=""):
            field = ft.TextField(label=label, value=str(c[key]), width=185,
                                 suffix_text=suffix, dense=True,
                                 keyboard_type=ft.KeyboardType.NUMBER)
            field.data = key
            return field

        self.f_target = num("Compression target", "target_mb", 1, 20000, suffix="MB")
        self.f_split = num("Split Markdown every", "split_mb", 0, 100, suffix="MB")
        self.f_dpi = num("Image quality", "image_dpi", 50, 600, suffix="DPI")
        self.f_workers = ft.TextField(
            label="Files at once", width=185, dense=True,
            value=str(c["workers"] or batch.default_workers()),
            keyboard_type=ft.KeyboardType.NUMBER,
            helper_text="Lower for very large files")

        self.lang_field = ft.TextField(label="Document languages", read_only=True,
                                       value=self._lang_text(), expand=True, dense=True)
        lang_row = ft.Row([self.lang_field,
                           ft.OutlinedButton("Choose…", on_click=self._choose_langs)])

        def check(label, key, hint=""):
            return ft.Checkbox(label=label, value=bool(c[key]), data=key, tooltip=hint)

        self.c_recursive = check("Include sub-folders", "recursive")
        self.c_auto = check("Narrow languages to what the document contains", "auto_langs")
        self.c_force = check("Force OCR (ignore the text layer)", "force_ocr")
        self.c_tables = check("Extract tables", "extract_tables")
        self.c_figs = check("Describe drawings and charts", "describe_figures")
        self.c_mermaid = check("Rebuild flowcharts as diagrams", "mermaid")
        self.c_export = check("Save figure pages as images", "export_figures")

        return ft.Column(spacing=10, controls=[
            ft.Row([self.f_target, self.f_split, self.f_dpi, self.f_workers],
                   wrap=True, spacing=12),
            lang_row,
            ft.Row([self.c_recursive, self.c_auto, self.c_force], wrap=True, spacing=16),
            ft.Row([self.c_tables, self.c_figs, self.c_mermaid, self.c_export],
                   wrap=True, spacing=16),
            ft.Text("Settings are remembered for next time.", size=11,
                    color=_colour("GREY")),
        ])

    # ------------------------------------------------------------ settings
    def _lang_text(self) -> str:
        codes = [c for c in self.cfg["ocr_lang"].split("+") if c]
        return ", ".join(ocr.LANG_NAMES.get(c, c).split(" (")[0] for c in codes) or "English"

    def _collect(self) -> dict:
        def as_num(field, key):
            try:
                return float(field.value) if isinstance(prefs.DEFAULTS[key], float) \
                    else int(float(field.value))
            except (TypeError, ValueError):
                return prefs.DEFAULTS[key]
        self.cfg.update({
            "target_mb": as_num(self.f_target, "target_mb"),
            "split_mb": as_num(self.f_split, "split_mb"),
            "image_dpi": as_num(self.f_dpi, "image_dpi"),
            "workers": as_num(self.f_workers, "workers"),
            "recursive": self.c_recursive.value, "auto_langs": self.c_auto.value,
            "force_ocr": self.c_force.value, "extract_tables": self.c_tables.value,
            "describe_figures": self.c_figs.value, "mermaid": self.c_mermaid.value,
            "export_figures": self.c_export.value,
        })
        return self.cfg

    def _save(self) -> None:
        try:
            prefs.save(self._collect())
        except Exception:
            pass

    def _choose_langs(self, _) -> None:
        if not self._available_langs:
            self._toast("No OCR engine found, so no languages are available.")
            return
        chosen = set(self.cfg["ocr_lang"].split("+"))
        boxes = [ft.Checkbox(label=ocr.LANG_NAMES.get(code, code),
                             value=code in chosen, data=code)
                 for code in self._available_langs if code != "osd"]

        def apply(_):
            picked = [b.data for b in boxes if b.value] or ["eng"]
            self.cfg["ocr_lang"] = "+".join(picked)
            self.lang_field.value = self._lang_text()
            dlg.open = False
            self.page.update()

        dlg = ft.AlertDialog(
            modal=True, title=ft.Text("Document languages"),
            content=ft.Container(width=420, height=340, content=ft.Column(
                scroll=ft.ScrollMode.AUTO, controls=[
                    ft.Text("Tick the languages that appear in the documents. "
                            "Two or three is best — each extra one slows reading "
                            "and can reduce accuracy on the others.", size=12),
                    *boxes])),
            actions=[ft.TextButton("Cancel", on_click=lambda _: self._close(dlg)),
                     ft.FilledButton("OK", on_click=apply)],
            actions_alignment=ft.MainAxisAlignment.END)
        self._open(dlg)

    def _open(self, dlg) -> None:
        if dlg not in self.page.overlay:
            self.page.overlay.append(dlg)
        dlg.open = True
        self.page.update()

    def _close(self, dlg) -> None:
        dlg.open = False
        self.page.update()

    def _toast(self, msg: str) -> None:
        bar = ft.SnackBar(ft.Text(msg))
        self.page.overlay.append(bar)
        bar.open = True
        self.page.update()

    # -------------------------------------------------------------- inputs
    def _on_files(self, e: ft.FilePickerResultEvent) -> None:
        if e.files:
            self._set_files([Path(f.path) for f in e.files if f.path])

    def _on_folder(self, e: ft.FilePickerResultEvent) -> None:
        if not e.path:
            return
        self.status.value = "Scanning folder…"
        self.page.update()
        exts = {x for op in registry.OPERATIONS for x in op.extensions}
        found = batch.collect_files([Path(e.path)], exts,
                                    bool(self.c_recursive.value))
        if not found:
            self.status.value = "Ready."
            self._toast("No supported documents found in that folder.")
            return
        self.cfg["last_folder"] = e.path
        self._set_files(found)

    def _set_files(self, files: list[Path]) -> None:
        groups: dict[str, list[Path]] = {}
        for f in files:
            groups.setdefault(f.suffix.lower(), []).append(f)
        skipped = 0
        if len(groups) > 1:
            ext = max(groups, key=lambda k: len(groups[k]))
            skipped = sum(len(v) for k, v in groups.items() if k != ext)
            files = groups[ext]

        self.files = files
        self.rows.clear()
        self.listview.controls.clear()
        for f in files[:2000]:
            row = FileRow(f)
            self.rows[str(f)] = row
            self.listview.controls.append(row)
        total = sum(f.stat().st_size for f in files if f.exists())
        self.drop_label.value = (f"{len(files)} document(s) ready" if len(files) > 1
                                 else files[0].name)
        self.drop_sub.value = f"{human(total)} in total" + (
            f" · {skipped} file(s) of another type ignored" if skipped else "")
        self.counts.value = f"{len(files)} queued"
        self._refresh_actions()
        self.status.value = "Ready."
        self.page.update()

    def _refresh_actions(self) -> None:
        ops = registry.operations_for(self.files)
        primary = next((o for o in ops if o.id == "pdf_to_md"), None)
        self.primary.disabled = primary is None
        self.primary.text = ("Convert to Markdown" if primary
                             else "Convert to Markdown (PDFs only)")
        self.secondary.controls = [
            ft.OutlinedButton(op.label, tooltip=op.description,
                              on_click=lambda _, o=op: self._start(o))
            for op in ops if op.id != "pdf_to_md"]

    # --------------------------------------------------------------- run
    def _start_by_id(self, op_id: str) -> None:
        op = next((o for o in registry.operations_for(self.files) if o.id == op_id), None)
        if op:
            self._start(op)

    def _start(self, op: registry.Operation) -> None:
        if self.worker and self.worker.is_alive():
            return
        if not self.files:
            return
        cfg = self._collect()
        prefs.save(cfg)
        workers = int(cfg["workers"] or batch.default_workers())
        opts = {k: cfg[k] for k in
                ("target_mb", "split_mb", "image_dpi", "ocr_dpi", "ocr_lang",
                 "force_ocr", "auto_langs", "extract_tables", "describe_figures",
                 "mermaid", "export_figures", "page_breaks")}
        opts["ocr_workers"] = workers if len(self.files) == 1 else 1

        self.cancel.clear()
        self.est = Estimator(sum(f.stat().st_size for f in self.files if f.exists()),
                             len(self.files))
        for row in self.rows.values():
            row.set("waiting", "")
        self.bar.value = 0
        self.cancel_btn.visible = True
        self.primary.disabled = True
        for c in self.secondary.controls:
            c.disabled = True
        self.status.value = f"{op.label} — starting…"
        self.page.update()

        self.worker = threading.Thread(target=self._run, args=(op, list(self.files),
                                                               opts, workers),
                                       daemon=True)
        self.worker.start()

    def _run(self, op, files, opts, workers) -> None:
        outputs: list[str] = []
        failures: list[str] = []
        try:
            if op.batch and len(files) > 1 and workers > 1 and op.parallel_safe:
                outputs, failures = batch.run_parallel(
                    op.id, files, opts, workers,
                    lambda p, m: self._tick(m),
                    self.cancel.is_set, on_file=self._file_done)
            else:
                with session_temp() as tmp:
                    ctx = OpContext(tmp, lambda p, m: self._tick(m),
                                    self.cancel.is_set, opts)
                    if op.batch and len(files) > 1:
                        for i, f in enumerate(files):
                            ctx.check_cancel()
                            self._mark(f, "running")
                            sub = ctx.scoped(100 * i / len(files),
                                             100 * (i + 1) / len(files))
                            try:
                                out = op.handler([f], sub)
                                outputs.append(str(out))
                                self._file_done(f, str(out), "")
                            except Cancelled:
                                raise
                            except Exception as exc:
                                failures.append(f"{f.name}: {exc}")
                                self._file_done(f, "", f"{type(exc).__name__}: {exc}")
                    else:
                        self._mark(files[0], "running")
                        out = op.handler(files, ctx)
                        outputs.append(str(out))
                        self._file_done(files[0], str(out), "")
        except Cancelled:
            self._finish("Cancelled. Temporary files removed.", outputs, failures)
            return
        except Exception as exc:
            self._finish(f"Failed: {type(exc).__name__}: {exc}", outputs, failures)
            return
        self._finish("", outputs, failures)

    # ------------------------------------------------------------- updates
    def _mark(self, path: Path, status: str, detail: str = "") -> None:
        row = self.rows.get(str(path))
        if row:
            row.set(status, detail)
            self._paint()

    def _file_done(self, src: Path, out: str, err: str) -> None:
        if self.est:
            try:
                self.est.advance(src.stat().st_size if src.exists() else 0)
            except OSError:
                self.est.advance(0)
            self.bar.value = self.est.fraction
            self.eta.value = self.est.remaining_text()
        if err:
            self._mark(src, "failed", err[:80])
        else:
            self._mark(src, "done", Path(out).name if out else "")
        if self.est:
            self.counts.value = (f"{self.est.done_items} of {self.est.total_items} done")
        self._paint(force=True)

    def _tick(self, message: str) -> None:
        if message:
            self.status.value = message
        if self.est and self.est.total_items <= 1:
            self.eta.value = self.est.remaining_text()
        self._paint()

    def _paint(self, force: bool = False) -> None:
        """Throttled repaint — 200 rows updating per page would stutter."""
        now = time.time()
        if force or now - self._last_paint > 0.25:
            self._last_paint = now
            try:
                self.page.update()
            except Exception:
                pass

    def _finish(self, message: str, outputs: list[str], failures: list[str]) -> None:
        self.cancel_btn.visible = False
        self.primary.disabled = False
        for c in self.secondary.controls:
            c.disabled = False
        self.bar.value = 1.0 if not message else self.bar.value
        self.eta.value = ""
        if message:
            self.status.value = message
        else:
            done = len(outputs)
            note = f"Finished — {done} file(s) converted"
            if failures:
                note += f", {len(failures)} failed"
            if self.est:
                note += f" ({self.est.summary()})"
            self.status.value = note
        self._paint(force=True)

    def _cancel(self, _) -> None:
        self.cancel.set()
        self.status.value = "Cancelling — documents already in progress will finish…"
        self._paint(force=True)

    def _reveal(self) -> None:
        import os
        import subprocess
        import sys
        target = str(config.RESULT_DIR)
        try:
            if sys.platform == "win32":
                os.startfile(target)              # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception:
            self._toast(f"Results are in {target}")


def main(argv: list[str] | None = None) -> None:
    import sys
    purge_temp_root()
    args = list(argv if argv is not None else sys.argv[1:])
    auto_op = None
    if "--op" in args:
        i = args.index("--op")
        auto_op = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    initial = [Path(a) for a in args if not a.startswith("-")]

    def target(page: ft.Page) -> None:
        App(page, initial, auto_op)

    ft.app(target=target)
