"""A small Chinese desktop interface for the existing BabelDOC translator."""

from __future__ import annotations

import asyncio
import logging
import queue
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog
from tkinter import messagebox
from tkinter import ttk

from desktop_settings import desktop_directory
from desktop_settings import load_settings
from desktop_settings import save_settings
from desktop_settings import settings_path
from tkinterdnd2 import COPY
from tkinterdnd2 import DND_FILES
from tkinterdnd2 import REFUSE_DROP
from tkinterdnd2 import TkinterDnD

APP_NAME = "纸间 · 论文译读"
BG = "#F5F2E9"
PAPER = "#FFFEFA"
INK = "#242820"
MUTED = "#858579"
GREEN = "#385E4B"
GREEN_LIGHT = "#E8EEE7"
RED = "#C66B53"
LINE = "#E8E4D9"


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


class QueueLogHandler(logging.Handler):
    def __init__(self, events: queue.Queue, secret: str) -> None:
        super().__init__(logging.INFO)
        self.events = events
        self.secret = secret

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        # BabelDOC's startup diagnostics sometimes echo translator configuration.
        if self.secret:
            message = message.replace(self.secret, "[密钥已隐藏]")
        self.events.put(("log", message))


class TranslatorApp:
    def __init__(self, settings_file: Path | None = None) -> None:
        self.settings_file = settings_file if settings_file is not None else settings_path()
        self.save_timer = None
        self.cancel_requested = threading.Event()
        self.root = TkinterDnD.Tk()
        self.root.title(APP_NAME)
        self.root.iconbitmap(str(resource_path("app.ico")))
        self.root.configure(bg=BG)
        self.root.geometry(f"{min(960, self.root.winfo_screenwidth() - 100)}x{min(740, self.root.winfo_screenheight() - 110)}")
        self.root.minsize(720, 460)
        self.events: queue.Queue = queue.Queue()
        self.running = False
        self.config = None
        self.log_handler = None
        self.pdf_path = tk.StringVar()
        self.output_path = tk.StringVar(value=str(desktop_directory()))
        self.base_url = tk.StringVar(value="https://api.deepseek.com")
        self.model = tk.StringVar(value="deepseek-chat")
        self.api_key = tk.StringVar()
        self.pages = tk.StringVar()
        self.output_mode = tk.StringVar(value="双语对照")
        self.status = tk.StringVar(value="等待一篇值得细读的论文")
        self.settings_status = tk.StringVar(value="API Key 将加密保存在本机，下次自动填入。")
        self.progress = tk.DoubleVar(value=0)
        self.log_lines: list[str] = []
        self._load_settings()
        self._style()
        self._build()
        self.root.drop_target_register(DND_FILES)
        self.root.dnd_bind("<<Drop>>", self._drop_pdf)
        for variable in (self.base_url, self.model, self.api_key):
            variable.trace_add("write", self._schedule_save)
        self.root.after(120, self._poll)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Paper.Horizontal.TProgressbar", troughcolor=LINE,
                        background=GREEN, bordercolor=LINE, lightcolor=GREEN,
                        darkcolor=GREEN, thickness=7)
        style.configure("Paper.TCombobox", fieldbackground=PAPER,
                        background=PAPER, foreground=INK, arrowcolor=GREEN,
                        padding=8)

    def _card(self, parent: tk.Widget, **kwargs) -> tk.Frame:
        return tk.Frame(parent, bg=PAPER, highlightbackground=LINE,
                        highlightthickness=1, **kwargs)

    def _label(self, parent: tk.Widget, text: str = "", **kwargs) -> tk.Label:
        kwargs.setdefault("bg", PAPER)
        kwargs.setdefault("fg", INK)
        kwargs.setdefault("font", ("Microsoft YaHei UI", 10))
        return tk.Label(parent, text=text, **kwargs)

    def _entry(self, parent: tk.Widget, variable: tk.StringVar,
               **kwargs) -> tk.Entry:
        return tk.Entry(parent, textvariable=variable, bg="#FAF9F4", fg=INK,
                        insertbackground=GREEN, relief="flat",
                        highlightthickness=1, highlightbackground=LINE,
                        highlightcolor=GREEN, font=("Microsoft YaHei UI", 10),
                        **kwargs)

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        body = tk.Frame(self.root, bg=BG)
        body.grid(row=0, column=0, sticky="nsew")
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        outer = tk.Frame(self.canvas, bg=BG, padx=28, pady=16)
        content_id = self.canvas.create_window(0, 0, window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(content_id, width=event.width))
        self.root.bind("<MouseWheel>", self._scroll)

        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 12))
        tk.Label(header, text="纸间", bg=BG, fg=GREEN,
                 font=("Microsoft YaHei UI", 30, "bold")).pack(side="left")
        tk.Label(header, text="PAPER  /  LANGUAGE  /  UNDERSTANDING",
                 bg=BG, fg=MUTED, font=("Georgia", 9)).pack(
                     side="left", padx=(14, 0), pady=(13, 0))
        tk.Label(header, text="PDF  ·  TRANSLATOR", bg=BG, fg=RED,
                 font=("Georgia", 9)).pack(side="right", pady=(14, 0))

        hero = tk.Frame(outer, bg=BG)
        hero.pack(fill="x", pady=(0, 12))
        tk.Label(hero, text="让每一篇论文，都读得更从容。", bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 19)).pack(anchor="w")
        tk.Label(hero, text="选择文献，设定译法；其余的，交给纸间。",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 10)).pack(
                     anchor="w", pady=(6, 0))

        doc = self._card(outer, padx=22, pady=17)
        doc.pack(fill="x", pady=(0, 13))
        self._section_title(doc, "01", "选择文献", "拖入一份 PDF，或点击浏览文件")
        row = tk.Frame(doc, bg=PAPER)
        row.pack(fill="x", pady=(14, 0))
        self.file_entry = self._entry(row, self.pdf_path)
        self.file_entry.pack(side="left", fill="x", expand=True, ipady=10, padx=(0, 10))
        self.file_entry.drop_target_register(DND_FILES)
        self.file_entry.dnd_bind("<<Drop>>", self._drop_pdf)
        self._button(row, "浏览文件", self._choose_pdf, outlined=True).pack(side="right")
        out = tk.Frame(doc, bg=PAPER)
        out.pack(fill="x", pady=(11, 0))
        self._label(out, "译稿存入", fg=MUTED, width=9, anchor="w").pack(side="left")
        self._entry(out, self.output_path).pack(side="left", fill="x",
                                                expand=True, ipady=8, padx=(0, 10))
        self._button(out, "更改", self._choose_output, outlined=True).pack(side="right")

        options = self._card(outer, padx=22, pady=17)
        options.pack(fill="x", pady=(0, 13))
        self._section_title(options, "02", "译读设置", "一些轻巧的选择")
        grid = tk.Frame(options, bg=PAPER)
        grid.pack(fill="x", pady=(14, 0))
        self._label(grid, "翻译方向", fg=MUTED).grid(row=0, column=0, sticky="w")
        self._label(grid, "生成版本", fg=MUTED).grid(row=0, column=1, sticky="w", padx=28)
        self._label(grid, "指定页码", fg=MUTED).grid(row=0, column=2, sticky="w", padx=15)
        self._label(grid, "英文 → 中文", fg=GREEN,
                    font=("Microsoft YaHei UI", 11, "bold")).grid(
                        row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(grid, textvariable=self.output_mode, state="readonly",
                     values=("双语对照", "仅中文译文", "两种版本"),
                     style="Paper.TCombobox", width=17).grid(
                         row=1, column=1, sticky="w", padx=28, pady=(3, 0))
        self._entry(grid, self.pages, width=17).grid(
            row=1, column=2, sticky="ew", padx=15, ipady=8, pady=(3, 0))
        tk.Label(grid, text="留空即翻译全文，例如：1-5,8", bg=PAPER,
                 fg=MUTED, font=("Microsoft YaHei UI", 8)).grid(
                     row=2, column=2, sticky="w", padx=15, pady=(4, 0))
        grid.columnconfigure(2, weight=1)

        service = self._card(outer, padx=22, pady=17)
        service.pack(fill="x", pady=(0, 15))
        self._section_title(service, "03", "翻译服务", "兼容 OpenAI 接口")
        srv = tk.Frame(service, bg=PAPER)
        srv.pack(fill="x", pady=(13, 0))
        for column, (label, var, width, secret) in enumerate((
                ("API 地址", self.base_url, 1, False),
                ("模型", self.model, 1, False),
                ("API Key", self.api_key, 1, True))):
            cell = tk.Frame(srv, bg=PAPER)
            cell.grid(row=0, column=column, sticky="ew",
                      padx=(0 if column == 0 else 12, 0))
            self._label(cell, label, fg=MUTED).pack(anchor="w", pady=(0, 6))
            self._entry(cell, var, width=width,
                        show="●" if secret else "").pack(fill="x", ipady=8)
        for col in range(3):
            srv.columnconfigure(col, weight=(2 if col == 0 else 1), uniform="service")
        saved_row = tk.Frame(service, bg=PAPER)
        saved_row.pack(fill="x", pady=(8, 0))
        self._label(saved_row, textvariable=self.settings_status, fg=MUTED,
                    font=("Microsoft YaHei UI", 8)).pack(side="left")
        self._button(saved_row, "清除密钥", self._clear_key, outlined=True).pack(side="right")

        footer = tk.Frame(self.root, bg=BG, padx=28, pady=12)
        footer.grid(row=1, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        state = tk.Frame(footer, bg=BG)
        state.grid(row=0, column=0, sticky="ew", padx=(0, 16))
        self._label(state, textvariable=self.status, bg=BG, fg=INK,
                    anchor="w", width=1, wraplength=390).pack(fill="x")
        self.bar = ttk.Progressbar(state, variable=self.progress, maximum=100,
                                   style="Paper.Horizontal.TProgressbar")
        self.bar.pack(fill="x", pady=(9, 0))
        self.action = self._button(footer, "开始翻译", self._start)
        self.action.grid(row=0, column=2, ipadx=17, ipady=4)
        self.cancel = self._button(footer, "取消", self._cancel, outlined=True)

        self.log = tk.Text(outer, height=2, bg=BG, fg=MUTED, relief="flat",
                           state="disabled", font=("Consolas", 8), wrap="word")
        self.log.pack(fill="x", pady=(7, 0))
        self._update_output_default()

    def _section_title(self, parent: tk.Widget, number: str,
                       title: str, subtitle: str) -> None:
        line = tk.Frame(parent, bg=PAPER)
        line.pack(fill="x")
        tk.Label(line, text=number, bg=GREEN_LIGHT, fg=GREEN,
                 font=("Georgia", 9), padx=8, pady=4).pack(side="left")
        tk.Label(line, text=title, bg=PAPER, fg=INK,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(
                     side="left", padx=(10, 9))
        tk.Label(line, text=subtitle, bg=PAPER, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(side="left")

    def _button(self, parent: tk.Widget, text: str, command,
                outlined: bool = False) -> tk.Button:
        return tk.Button(parent, text=text, command=command,
                         bg=PAPER if outlined else GREEN,
                         fg=GREEN if outlined else "#FFFEFA",
                         activebackground=GREEN_LIGHT if outlined else "#2F5140",
                         activeforeground=GREEN if outlined else "#FFFEFA",
                         relief="flat", bd=0, padx=15, pady=9,
                         cursor="hand2", font=("Microsoft YaHei UI", 9,
                                                 "bold" if not outlined else "normal"),
                         highlightthickness=1 if outlined else 0,
                         highlightbackground=LINE)

    def _load_settings(self) -> None:
        try:
            saved = load_settings(self.settings_file)
            self.base_url.set(saved.get("base_url", self.base_url.get()))
            self.model.set(saved.get("model", self.model.get()))
            self.api_key.set(saved.get("api_key", ""))
        except (OSError, ValueError) as error:
            self.settings_status.set("本地设置读取失败，请重新填写。")

    def _save_settings(self) -> bool:
        if self.save_timer is not None:
            self.root.after_cancel(self.save_timer)
            self.save_timer = None
        try:
            save_settings(self.settings_file, self.base_url.get().strip(),
                          self.model.get().strip(), self.api_key.get().strip())
            self.settings_status.set("设置已保存 · 密钥由当前 Windows 账户加密" if self.api_key.get() else "设置已保存 · 当前未保存密钥")
            return True
        except (OSError, ValueError):
            self.settings_status.set("保存失败，请检查本机设置文件夹的写入权限。")
            return False

    def _schedule_save(self, *_args) -> None:
        if self.save_timer is not None:
            self.root.after_cancel(self.save_timer)
        self.save_timer = self.root.after(700, self._save_settings)

    def _clear_key(self) -> None:
        self.api_key.set("")
        self._save_settings()

    def _scroll(self, event) -> None:
        if self.canvas.bbox("all")[3] > self.canvas.winfo_height():
            self.canvas.yview_scroll(-int(event.delta / 120), "units")

    def _drop_pdf(self, event):
        if self.running:
            self.status.set("请等当前翻译结束后再更换文献。")
            return REFUSE_DROP
        paths = self.root.tk.splitlist(event.data)
        if len(paths) != 1 or not Path(paths[0]).is_file() or Path(paths[0]).suffix.lower() != ".pdf":
            self.status.set("请一次拖入一份 PDF 文档。")
            return REFUSE_DROP
        self.pdf_path.set(paths[0])
        self.status.set(f"已选择：{Path(paths[0]).name}")
        return COPY

    def _update_output_default(self) -> None:
        if not self.output_path.get():
            self.output_path.set(str(desktop_directory()))

    def _choose_pdf(self) -> None:
        path = filedialog.askopenfilename(title="选择论文 PDF",
                                          filetypes=(("PDF 文档", "*.pdf"),))
        if path:
            self.pdf_path.set(path)

    def _choose_output(self) -> None:
        path = filedialog.askdirectory(title="选择译稿文件夹")
        if path:
            self.output_path.set(path)

    def _start(self) -> None:
        source = Path(self.pdf_path.get().strip())
        if not source.is_file() or source.suffix.lower() != ".pdf":
            messagebox.showwarning("选择 PDF", "请先选择一份有效的 PDF 文档。")
            return
        if not self.api_key.get().strip():
            messagebox.showwarning("填写 API Key", "请填写翻译服务的 API Key。")
            return
        if not self.base_url.get().strip() or not self.model.get().strip():
            messagebox.showwarning("检查服务设置", "请填写 API 地址和模型名称。")
            return
        self.running = True
        self.cancel_requested.clear()
        self.action.configure(state="disabled")
        self.cancel.grid(row=0, column=1, padx=(0, 8))
        self.progress.set(0)
        self.status.set("正在整理文献，准备开始翻译…")
        self._save_settings()
        self.job = {
            "pdf": self.pdf_path.get(), "output": self.output_path.get().strip(),
            "pages": self.pages.get().strip(), "model": self.model.get().strip(),
            "base_url": self.base_url.get().strip(), "api_key": self.api_key.get().strip(),
            "mode": self.output_mode.get(),
        }
        self.log_handler = QueueLogHandler(self.events, self.job["api_key"])
        logging.getLogger().addHandler(self.log_handler)
        threading.Thread(target=self._translate, daemon=True).start()

    def _translate(self) -> None:
        try:
            from babeldoc.docvision.doclayout import DocLayoutModel
            from babeldoc.format.pdf.high_level import async_translate
            from babeldoc.format.pdf.translation_config import TranslationConfig
            from babeldoc.format.pdf.translation_config import WatermarkOutputMode
            from babeldoc.translator.translator import OpenAITranslator
            from babeldoc.translator.translator import set_translate_rate_limiter

            lang_in, lang_out = "en", "zh"
            translator = OpenAITranslator(
                lang_in=lang_in, lang_out=lang_out, model=self.job["model"],
                base_url=self.job["base_url"], api_key=self.job["api_key"],
                enable_json_mode_if_requested=True,
            )
            set_translate_rate_limiter(2)
            output = Path(self.job["output"] or desktop_directory())
            output.mkdir(parents=True, exist_ok=True)
            layout = DocLayoutModel.load_onnx()
            if self.cancel_requested.is_set():
                return
            self.config = TranslationConfig(
                input_file=self.job["pdf"], output_dir=str(output),
                pages=self.job["pages"] or None, translator=translator,
                lang_in=lang_in, lang_out=lang_out,
                doc_layout_model=layout,
                no_dual=self.job["mode"] == "仅中文译文",
                no_mono=self.job["mode"] == "双语对照",
                watermark_output_mode=WatermarkOutputMode.Watermarked,
                use_rich_pbar=False,
                qps=2, auto_extract_glossary=False,
            )

            async def run() -> None:
                async for event in async_translate(self.config):
                    if event["type"] in ("progress_start", "progress_update", "progress_end"):
                        self.events.put(("progress", event))
                    elif event["type"] == "finish":
                        result = event.get("translate_result")
                        paths = [str(path) for path in (
                            getattr(result, "mono_pdf_path", None),
                            getattr(result, "dual_pdf_path", None)) if path]
                        self.events.put(("done", paths))
                    elif event["type"] == "error":
                        self.events.put(("error", str(event.get("error", "翻译失败"))))

            asyncio.run(run())
        except Exception:
            details = traceback.format_exc()
            details = details.replace(self.job["api_key"], "[密钥已隐藏]")
            self.events.put(("error", details))
        finally:
            self.events.put(("finished", None))

    def _cancel(self) -> None:
        self.cancel_requested.set()
        if self.config is not None:
            self.config.cancel_translation()
        self.status.set("正在结束当前步骤…")

    def _poll(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                if self.cancel_requested.is_set():
                    if self.config is not None:
                        self.config.cancel_translation()
                    continue
                value = payload.get("overall_progress", payload.get("stage_progress", 0))
                self.progress.set(max(0, min(100, float(value or 0))))
                self.status.set(f"{payload.get('stage', '正在翻译')}  ·  {self.progress.get():.0f}%")
            elif kind == "log":
                self.log_lines.append(payload)
                self.log_lines = self.log_lines[-4:]
                self.log.configure(state="normal")
                self.log.delete("1.0", "end")
                self.log.insert("end", "\n".join(self.log_lines))
                self.log.configure(state="disabled")
            elif kind == "done":
                if payload:
                    self.progress.set(100)
                    self.status.set("译稿已完成，静候阅读。")
                    self._open_results(payload)
                else:
                    self.status.set("翻译已结束，请检查运行记录。")
            elif kind == "error":
                self.status.set("翻译未能完成")
                secret = self.job.get("api_key", "")
                messagebox.showerror("翻译遇到问题", (payload.replace(secret, "[密钥已隐藏]") if secret else payload)[-3500:])
            elif kind == "finished":
                self.running = False
                self.config = None
                if self.log_handler is not None:
                    logging.getLogger().removeHandler(self.log_handler)
                    self.log_handler = None
                self.action.configure(state="normal")
                self.cancel.grid_remove()
                if self.cancel_requested.is_set():
                    self.status.set("翻译已取消")
                self.job = {}
        self.root.after(120, self._poll)

    def _open_results(self, paths: list[str]) -> None:
        message = "\n".join(paths)
        if messagebox.askyesno("译稿已完成", f"已生成：\n{message}\n\n要打开译稿文件夹吗？"):
            webbrowser.open(str(Path(paths[0]).parent))

    def _close(self) -> None:
        if self.running and not messagebox.askyesno("翻译正在进行", "退出将中断翻译，仍要退出吗？"):
            return
        if self.config is not None:
            self.config.cancel_translation()
        self._save_settings()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    if len(sys.argv) == 3 and sys.argv[1] in ("--self-check", "--check-translation"):
        from desktop_check import run_checks
        sys.exit(run_checks(Path(sys.argv[2]), translation=sys.argv[1] == "--check-translation"))
    TranslatorApp().run()
