"""The application window.

Design rules this file follows, because the previous UI grew into a wall of controls:

* **The main window only holds what is used every day.** Two things: translate what is
  on screen, and translate text you paste. Everything else - engines, credentials,
  glossary, overlay appearance, recognition tuning, logs - lives behind one 设置 button.
* **Each tab has one obvious primary action.** A first-time user should not have to
  read anything to know what to click.
* **State is shown, not implied.** Region, engine, language direction and the last
  status message are always visible.
* Content is separated into cards on a single muted palette, with generous spacing.
"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import __version__ as APP_VERSION
from . import config as config_mod
from . import engine as engine_mod
from .config import AppConfig, Region
from .glossary import (Glossary, GlossaryError, Rule,  # noqa: F401
                        trade_glossary_path)
from .hotkeys import HotkeyManager
from .overlay import Overlay
from .pipeline import ERROR, RESULT, STATS, STATUS, TARGET, Event, Pipeline
from .region_picker import RegionPicker
from .docx_io import translate_docx
from .image_tool import IMAGE_SUFFIXES, translate_image
from .pdf_tool import PDF_SUFFIXES, PdfError, translate_pdf
from .text_tool import (STRUCTURED_SUFFIXES, TEXT_SUFFIXES, read_text_file,
                          translate_text)
from .xlsx_io import translate_xlsx

# ---------------------------------------------------------------- palette
BG = "#17171a"
CARD = "#212126"
CARD_HI = "#2a2a31"
BORDER = "#33333c"
TEXT = "#e8e8ec"
MUTED = "#9a9aa5"
ACCENT = "#3b82f6"
ACCENT_HI = "#2563eb"
OK = "#22c55e"
WARN = "#f59e0b"
DANGER = "#ef4444"

UI_FONT = ("Microsoft YaHei UI", 10)
BODY_FONT = ("Microsoft YaHei UI", 10)
TITLE_FONT = ("Microsoft YaHei UI", 12, "bold")
RESULT_FONT = ("Microsoft YaHei UI", 11)
ABOUT_TEXT = f"""
这个软件几乎全部建立在别人的工作上。以下组件要求署名，商业使用请保留本节。

【离线翻译模型】Helsinki-NLP OPUS-MT
  俄语到中文的离线路线经英语中转，链路上包含 opus-mt-ru-en，
  授权 CC-BY-4.0（可商用，需署名）；部分语言对为 Apache-2.0。
  ONNX 格式由 Xenova 导出。NLLB-200 为 CC-BY-NC-4.0（禁止商用），默认不安装。

【文字识别】RapidOCR / PP-OCRv4 —— Apache-2.0

【中俄词典】FreeDict 的 zho-rus，版本 2025.11.23，156,590 个词条
  上游 WikDict，基于 Wiktionary，经 DBnary 生成；维护者 Karl Bartel
  授权 CC BY-SA 3.0 Unported —— 可以商用，必须署名，相同方式共享
  完整说明见 app/data/README.md（就是本页「打开署名文件」那个）

【俄语词表】wordfreq 3.1.1 —— MIT / CC BY-SA 4.0

【本项目的许可】MIT，见软件目录下的 LICENSE

【联网引擎】选中的引擎会把识别到的文字发送给对应服务商，条款见各服务商。
  离线引擎不会发送任何内容。联网引擎可以在发送前遮住公司名、税号、
  银行账号、电话、邮箱和网址，翻完自动还原。

关于 CC BY-SA 的「相同方式共享」：它只作用于词典数据本身（以及由它派生的
反向索引），不要求你的程序代码开源。如果要去掉这条约束，删掉
app/data/zh_ru_dict.db 即可，程序会自动跳过词典功能。
"""

#: Shown on the 关于 tab. Kept as one string so the whole notice can be copied in one
#: click, which is what a customer needs when their own lawyer asks for it.

LANGUAGES: list[tuple[str, str]] = [
    ("en", "英语"),
    ("zh", "中文"),
    ("ru", "俄语"),
    ("ja", "日语"),
    ("ko", "韩语"),
    ("fr", "法语"),
    ("de", "德语"),
    ("es", "西班牙语"),
]
LANG_LABEL = {code: name for code, name in LANGUAGES}
LANG_CODE = {name: code for code, name in LANGUAGES}


def _pretty_region(cfg: AppConfig) -> str:
    region = cfg.region
    if getattr(cfg, "target_mode", "region") == "window":
        from .window_target import find_window

        handle = find_window(cfg.target_window_title, cfg.target_window_process)
        state = "窗口已关闭或被最小化" if not handle else "跟随中"
        return (f"跟随窗口：{cfg.target_window_title or '未选择'}（{state}）\n"
                f"窗口内范围 {region.width} × {region.height}"
                f"，距左上角 ({region.left}, {region.top})")
    if not region.valid:
        return "还没有框选区域"
    return f"固定屏幕区域  {region.width} × {region.height}   位置 ({region.left}, {region.top})"


class App(tk.Tk):
    def __init__(self, cfg: AppConfig) -> None:
        super().__init__()
        self.cfg = cfg
        self.title("守译")
        self.configure(bg=BG)
        # Tall enough that the screen tab's content fits without squeezing the
        # translation box; grid shrinks the weighted row first when space runs out.
        self.minsize(760, 760)
        self.geometry("860x700")
        self._apply_window_icon()

        self.events: queue.Queue = queue.Queue(maxsize=512)
        self.pipeline: Pipeline | None = None
        self.hotkeys: HotkeyManager | None = None
        self.hotkey_queue: queue.Queue = queue.Queue(maxsize=32)
        self._text_thread: threading.Thread | None = None
        self._text_stop = threading.Event()
        #: Unit for the text tab: "auto", "paragraph" or "line". Importing a subtitle or a
        #: spreadsheet sets "line", because there the line break is the record itself and
        #: joining lines would destroy the structure.
        self._text_mode = "auto"
        self._last_status = ""
        self._warned: set[str] = set()

        self.overlay = Overlay(self)
        #: Referenced by option callbacks, so it must exist before any widget is built.
        self.capture_exclude_var = tk.BooleanVar(value=True)
        #: Created once, here, and shared by both tabs' language rows. Building them
        #: inside the row helper would orphan the first tab's widgets: the second call
        #: would rebind self.src_var to a fresh variable that only the second tab
        #: watches, leaving the first tab's combobox empty forever.
        self.src_var = tk.StringVar()
        self.tgt_var = tk.StringVar()

        self._build_styles()
        self._build_header()
        self._build_tabs()
        self._build_statusbar()
        self._sync_from_config()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(60, self._drain)
        self._start_hotkeys()

    # ------------------------------------------------------------ icon/styles
    def _apply_window_icon(self) -> None:
        """Give the window the app icon instead of Tk's default feather."""
        candidates = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "ScreenTranslator", "app.ico"),
            os.path.join(config_mod.APP_DIR, "assets", "app.ico"),
        ]
        for icon in candidates:
            if not os.path.isfile(icon):
                continue
            try:
                self.iconbitmap(default=icon)
                return
            except Exception:  # noqa: BLE001
                try:
                    self.iconbitmap(icon)
                    return
                except Exception:  # noqa: BLE001
                    continue

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:  # noqa: BLE001
            pass

        style.configure(".", background=BG, foreground=TEXT, font=UI_FONT)
        for name in ("TFrame", "TLabel", "TLabelframe", "TLabelframe.Label"):
            style.configure(name, background=BG, foreground=TEXT)
        style.configure("TLabel", font=UI_FONT)
        style.configure("Card.TFrame", background=CARD)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)
        style.configure("Muted.TLabel", background=BG, foreground=MUTED)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=TITLE_FONT)
        style.configure("Field.TLabel", background=BG, foreground=MUTED)
        style.configure("OK.TLabel", background=BG, foreground=OK)
        style.configure("Warn.TLabel", background=BG, foreground=WARN)

        style.configure("TButton", padding=(14, 7), background=CARD_HI,
                        foreground=TEXT, borderwidth=0, focusthickness=0)
        style.map("TButton",
                  background=[("active", BORDER), ("disabled", CARD)],
                  foreground=[("disabled", MUTED)])
        style.configure("Primary.TButton", padding=(20, 10), background=ACCENT,
                        foreground="#ffffff", font=("Microsoft YaHei UI", 11, "bold"))
        style.map("Primary.TButton",
                  background=[("active", ACCENT_HI), ("disabled", CARD)],
                  foreground=[("disabled", MUTED)])
        style.configure("Danger.TButton", padding=(16, 9), background=CARD_HI,
                        foreground=DANGER)
        style.map("Danger.TButton", background=[("active", BORDER)])

        style.configure("TEntry", fieldbackground=CARD, foreground=TEXT,
                        insertcolor=TEXT, borderwidth=0, padding=6)
        style.configure("TCombobox", fieldbackground=CARD, background=CARD,
                        foreground=TEXT, arrowcolor=MUTED, borderwidth=0, padding=5)
        style.map("TCombobox", fieldbackground=[("readonly", CARD)],
                  foreground=[("readonly", TEXT)])
        self.option_add("*TCombobox*Listbox.background", CARD)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

        style.configure("TCheckbutton", background=BG, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
        style.map("Card.TCheckbutton", background=[("active", CARD)])
        style.configure("TNotebook", background=BG, borderwidth=0, tabmargins=(0, 4, 0, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED,
                        padding=(22, 9), borderwidth=0, font=("Microsoft YaHei UI", 10))
        style.map("TNotebook.Tab",
                  background=[("selected", CARD)],
                  foreground=[("selected", TEXT)])
        style.configure("TSeparator", background=BORDER)
        style.configure("TScale", background=BG, troughcolor=CARD)
        style.configure("Vertical.TScrollbar", background=CARD, troughcolor=BG,
                        borderwidth=0, arrowcolor=MUTED)

    # ---------------------------------------------------------------- layout
    def _build_header(self) -> None:
        # The root uses grid, not pack: with pack, the notebook's expand=True is
        # allocated the leftover space first and later widgets (the status bar) get
        # squeezed to one pixel.
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        header = ttk.Frame(self, padding=(18, 14, 18, 8))
        header.grid(row=0, column=0, sticky="ew")

        ttk.Label(header, text="守译", style="Title.TLabel").pack(side="left")

        ttk.Button(header, text="设置", command=self.open_settings).pack(side="right")
        self.engine_var = tk.StringVar()
        engine_box = ttk.Combobox(header, textvariable=self.engine_var, state="readonly",
                                  width=26, values=list(engine_mod.engine_choices().values()))
        engine_box.pack(side="right", padx=(0, 10))
        engine_box.bind("<<ComboboxSelected>>", lambda _e: self._on_engine_change())
        self.engine_box = engine_box
        ttk.Label(header, text="引擎", style="Muted.TLabel").pack(side="right", padx=(0, 6))

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 8))
        self.notebook.add(self._tab_screen(self.notebook), text="屏幕翻译")
        self.notebook.add(self._tab_text(self.notebook), text="文本翻译")

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self, padding=(18, 0, 18, 12))
        bar.grid(row=2, column=0, sticky="ew")
        self.status_var = tk.StringVar(value="就绪")
        self.timing_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        ttk.Label(bar, textvariable=self.timing_var, style="Muted.TLabel").pack(side="right")

    # ------------------------------------------------------------ language row
    def _language_row(self, parent: tk.Misc) -> ttk.Frame:
        """The language pair, shared by both tabs (⇄ swaps the direction)."""
        row = ttk.Frame(parent)
        ttk.Label(row, text="语言", style="Muted.TLabel").pack(side="left")
        src_box = ttk.Combobox(row, textvariable=self.src_var, state="readonly", width=12,
                               values=list(LANG_CODE))
        src_box.pack(side="left", padx=(10, 0))
        ttk.Button(row, text="⇄", width=3, command=self.swap_languages).pack(
            side="left", padx=8)
        tgt_box = ttk.Combobox(row, textvariable=self.tgt_var, state="readonly", width=12,
                               values=list(LANG_CODE))
        tgt_box.pack(side="left")
        src_box.bind("<<ComboboxSelected>>", lambda _e: self._on_language_change())
        tgt_box.bind("<<ComboboxSelected>>", lambda _e: self._on_language_change())
        return row

    def _card(self, parent: tk.Misc, padding: tuple[int, ...] = (16, 14)) -> ttk.Frame:
        return ttk.Frame(parent, style="Card.TFrame", padding=padding)

    # ------------------------------------------------------------- screen tab
    def _tab_screen(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        # grid again: the translation box is the thing worth the spare room, so it gets
        # the weight instead of being squeezed by whatever is packed around it.
        tab.columnconfigure(0, weight=1)
        # The translation row takes the slack, but it must never be squeezed away: at the
        # window's minimum height the grid shrank it to a single line, so a four-line
        # translation looked like one line and the rest was unreachable. A minimum height
        # here, and a taller window minimum, keeps the whole answer visible.
        tab.rowconfigure(7, weight=1, minsize=128)
        tab.rowconfigure(5, weight=0, minsize=58)
        row = 0

        self._language_row(tab).grid(row=row, column=0, sticky="ew", pady=(0, 14))
        row += 1

        region_card = self._card(tab)
        region_card.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(region_card, text="翻译哪里", style="CardMuted.TLabel").pack(anchor="w")
        self.region_var = tk.StringVar()
        ttk.Label(region_card, textvariable=self.region_var, style="Card.TLabel",
                  font=RESULT_FONT, wraplength=560, justify="left").pack(
            anchor="w", pady=(4, 10))
        actions = ttk.Frame(region_card, style="Card.TFrame")
        actions.pack(anchor="w")
        ttk.Button(actions, text="选择窗口  ( 推荐 )", command=self.pick_window).pack(
            side="left")
        ttk.Button(actions, text="在窗口内框选", command=self.frame_inside_window).pack(
            side="left", padx=8)
        ttk.Button(actions, text="框选屏幕区域", command=self.pick_region).pack(side="left")
        # Picking the wrong window used to be one-way: the only way back to "nothing
        # selected" was restarting the program.
        ttk.Button(actions, text="清除选区", command=self.clear_region).pack(
            side="left", padx=8)
        ttk.Label(region_card,
                  text="选窗口后，窗口拖到哪译文就跟到哪；框选屏幕区域则固定在原地。",
                  style="CardMuted.TLabel", wraplength=560,
                  justify="left").pack(anchor="w", pady=(8, 0))
        row += 1

        run_row = ttk.Frame(tab)
        run_row.grid(row=row, column=0, sticky="ew", pady=(0, 14))
        self.start_btn = ttk.Button(run_row, text="▶  开始翻译  ( F9 )",
                                    style="Primary.TButton", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(run_row, text="⏸  暂停  ( F11 )",
                                    command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=10)
        self.stop_btn = ttk.Button(run_row, text="■  停止  ( F10 )",
                                   style="Danger.TButton", command=self.stop)
        self.stop_btn.pack(side="left")
        self.overlay_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(run_row, text="显示悬浮字幕", variable=self.overlay_var,
                        command=self._on_overlay_toggle).pack(side="left", padx=(14, 0))
        row += 1

        ttk.Separator(tab, orient="horizontal").grid(row=row, column=0, sticky="ew",
                                                     pady=(0, 14))
        row += 1

        ttk.Label(tab, text="识别到的原文", style="Muted.TLabel").grid(
            row=row, column=0, sticky="w")
        row += 1
        self.source_text = self._readout(tab, height=2)
        self.source_text.grid(row=row, column=0, sticky="ew", pady=(4, 0))
        row += 1

        ttk.Label(tab, text="译文", style="Muted.TLabel").grid(
            row=row, column=0, sticky="w", pady=(12, 0))
        row += 1
        self.result_text = self._readout(tab, height=3, strong=True)
        self.result_text.grid(row=row, column=0, sticky="nsew", pady=(4, 0))
        return tab

    def _readout(self, parent: tk.Misc, height: int, strong: bool = False) -> tk.Text:
        """A read-only output box. Placement is the caller's job."""
        box = tk.Text(parent, height=height, wrap="word", bg=CARD, fg=TEXT, relief="flat",
                      padx=12, pady=10, insertwidth=0, borderwidth=0,
                      font=RESULT_FONT if strong else BODY_FONT,
                      highlightthickness=1, highlightbackground=BORDER,
                      highlightcolor=BORDER)
        box.configure(state="disabled")
        return box

    def _set_text(self, widget: tk.Text, value: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value or "")
        widget.configure(state="disabled")

    # --------------------------------------------------------------- text tab
    def _tab_text(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        # grid, so the button row keeps its height while the panes absorb the slack.
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        self._language_row(tab).grid(row=0, column=0, sticky="ew", pady=(0, 14))

        panes = ttk.Frame(tab)
        panes.grid(row=1, column=0, sticky="nsew")
        panes.columnconfigure(0, weight=1, uniform="pane")
        panes.columnconfigure(1, weight=1, uniform="pane")
        panes.rowconfigure(1, weight=1)

        ttk.Label(panes, text="原文（可粘贴，或导入文件）",
                  style="Muted.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(panes, text="译文", style="Muted.TLabel").grid(
            row=0, column=1, sticky="w", padx=(14, 0), pady=(0, 4))

        self.text_in = tk.Text(panes, wrap="word", bg=CARD, fg=TEXT, relief="flat",
                               padx=12, pady=10, borderwidth=0, font=BODY_FONT,
                               highlightthickness=1, highlightbackground=BORDER,
                               insertbackground=TEXT, insertwidth=1)
        self.text_in.grid(row=1, column=0, sticky="nsew")
        self.text_out = tk.Text(panes, wrap="word", bg=CARD, fg=TEXT, relief="flat",
                                padx=12, pady=10, borderwidth=0, font=BODY_FONT,
                                highlightthickness=1, highlightbackground=BORDER,
                                insertbackground=TEXT, insertwidth=1)
        self.text_out.grid(row=1, column=1, sticky="nsew", padx=(14, 0))

        buttons = ttk.Frame(tab)
        buttons.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        self.text_run_btn = ttk.Button(buttons, text="翻译", style="Primary.TButton",
                                       command=self.run_text_translation)
        self.text_run_btn.pack(side="left")
        ttk.Button(buttons, text="清空", command=self.clear_text).pack(side="left", padx=8)
        ttk.Button(buttons, text="复制译文", command=self.copy_translation).pack(side="left")
        ttk.Button(buttons, text="导入文件", command=self.import_file).pack(
            side="left", padx=8)
        ttk.Button(buttons, text="保存译文", command=self.save_translation).pack(side="left")
        # A document is not pasted INTO the panes: it is translated whole and written
        # back out, so it sits at the end of the row as its own action.
        ttk.Button(buttons, text="翻译文档…", command=self.translate_document).pack(
            side="left", padx=8)
        # A photograph of a contract is what actually arrives over WeChat, and there is
        # nothing to paste: recognition has to happen first, so it is its own action.
        ttk.Button(buttons, text="翻译图片…", command=self.translate_picture).pack(
            side="left")
        # A PDF has no layout-preserving output format here, so it lands in the panes
        # rather than being written back out - which also makes 复制译文 / 保存译文 work.
        ttk.Button(buttons, text="翻译 PDF…", command=self.translate_pdf).pack(
            side="left", padx=8)

        # Dictionary lookup: a term in a supplier's message is often not something the
        # translator can render, and a dictionary shows every sense at once.
        lookup = ttk.Frame(tab)
        lookup.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(lookup, text="查词", style="Muted.TLabel").pack(side="left")
        self.lookup_var = tk.StringVar()
        entry = ttk.Entry(lookup, textvariable=self.lookup_var, width=22)
        entry.pack(side="left", padx=(8, 6))
        entry.bind("<Return>", lambda _e: self.lookup_word())
        ttk.Button(lookup, text="查询", command=self.lookup_word).pack(side="left")
        self.lookup_result = tk.StringVar(value="")
        ttk.Label(lookup, textvariable=self.lookup_result, style="Muted.TLabel").pack(
            side="left", padx=12)

        self.bind("<Control-Return>", lambda _e: self.run_text_translation())
        return tab

    # ------------------------------------------------------- dictionary lookup
    def lookup_word(self) -> None:
        """Show the dictionary's senses for a term, in the current direction."""
        from . import dictlookup

        term = self.lookup_var.get().strip()
        if not term:
            try:
                term = self.text_in.get("sel.first", "sel.last").strip()
            except tk.TclError:
                term = ""
        if not term:
            self.lookup_result.set("")
            self._status("先输入一个词，或在上面选中一个词")
            return
        if not dictlookup.available():
            self.lookup_result.set("（词典文件缺失）")
            self._status("找不到词典文件 app/data/zh_ru_dict.db")
            return
        hits = dictlookup.lookup(term, self.cfg.source_lang, self.cfg.target_lang)
        if hits:
            self.lookup_result.set("｜".join(hits))
            self._status(f"词典：{term} → {'｜'.join(hits)}")
        else:
            self.lookup_result.set("（词典未收录）")
            self._status(f"词典里没有「{term}」")

    # --------------------------------------------------------------- settings
    def open_settings(self) -> None:
        SettingsWindow(self)

    # ------------------------------------------------------------------ state
    def _sync_from_config(self) -> None:
        cfg = self.cfg
        self.src_var.set(LANG_LABEL.get(cfg.source_lang, cfg.source_lang))
        self.tgt_var.set(LANG_LABEL.get(cfg.target_lang, cfg.target_lang))
        labels = engine_mod.engine_choices()
        self.engine_box.configure(values=list(labels.values()))
        self.engine_var.set(labels.get(cfg.translate.engine,
                                       next(iter(labels.values()), "")))
        self.overlay_var.set(cfg.overlay.enabled)
        self.capture_exclude_var.set(True)
        self.region_var.set(_pretty_region(cfg))
        self._refresh_running_buttons()

    def _refresh_running_buttons(self) -> None:
        pipeline = self.pipeline
        alive = pipeline is not None and pipeline.is_alive()
        stopping = bool(pipeline is not None and pipeline.stopping)
        paused = bool(pipeline is not None and pipeline.paused and not stopping)

        self.start_btn.state(["disabled"] if alive else ["!disabled"])
        self.pause_btn.state(["!disabled"] if alive and not stopping else ["disabled"])
        self.pause_btn.configure(text="▶  继续  ( F11 )" if paused else "⏸  暂停  ( F11 )")
        self.stop_btn.state(["!disabled"] if alive and not stopping else ["disabled"])

    def toggle_pause(self) -> None:
        pipeline = self.pipeline
        if pipeline is None or not pipeline.is_alive():
            self._status("还没有在翻译")
            return
        if pipeline.paused:
            pipeline.resume()
            self._status("已继续")
        else:
            pipeline.pause()
            self._status("已暂停（模型还在内存里，继续是秒开的）")
        self._refresh_running_buttons()

    def start(self) -> None:
        pipeline = self.pipeline
        if pipeline is not None:
            if pipeline.is_alive():
                # Guard is on is_alive(), not on the reference being None: dropping the
                # reference the moment Stop is pressed would let a second pipeline start
                # while the first is still winding down, running two captures at once.
                self._status("正在运行中；要用「继续」恢复暂停的翻译")
                return
            self.pipeline = None
        if not self.cfg.region.valid:
            messagebox.showinfo(
                "先选择翻译哪里",
                "点「选择窗口」选一个窗口（推荐），或用「框选屏幕区域」框一块地方。",
                parent=self)
            return
        self._apply_overlay_settings()
        self.pipeline = Pipeline(self.cfg, self.events)
        self.pipeline.start()
        self._refresh_running_buttons()
        self._status("运行中")

    def stop(self) -> None:
        pipeline = self.pipeline
        # Stopping means the screen is no longer being read, so the last thing it said
        # should not stay on screen looking live. Clearing here rather than on the
        # worker thread, because the worker is about to die.
        self.overlay.hide()
        self._set_text(self.source_text, "")
        self._set_text(self.result_text, "")
        if pipeline is None or not pipeline.is_alive():
            self.pipeline = None
            self._refresh_running_buttons()
            self._status("已停止")
            return
        pipeline.stop()
        self._status("正在停止…")
        self._refresh_running_buttons()
        self._await_stop(pipeline, 0)

    def _await_stop(self, pipeline, waited: int) -> None:
        """Keep the reference until the worker really exits.

        Stopping cannot be instant - the loop may be mid-OCR, mid-translation, or still
        loading a model. Reporting "stopped" while the thread is alive is what allowed a
        second pipeline to be started on top of a dying one.

        The wait therefore has no give-up point. Stopping while the translation model is
        still loading measurably takes ~20 s here, so a fixed budget that expires first
        is worse than no budget: it re-enables Start and hands back exactly the bug
        above. Past the fast poll the interval just gets coarser; the reference is only
        released once the thread is genuinely gone.
        """
        if pipeline.is_alive():
            if waited < 150:                          # fast poll for the first ~15 s
                self.after(100, lambda: self._await_stop(pipeline, waited + 1))
            else:
                if waited == 150:
                    # Still winding down - say so, rather than leaving a dead-looking
                    # "正在停止…" behind a disabled Start button.
                    self._status("还在停止中…后台线程在收尾（模型正在加载时会久一些）")
                self.after(250, lambda: self._await_stop(pipeline, waited + 1))
            return
        if self.pipeline is pipeline:
            self.pipeline = None
        self._refresh_running_buttons()
        # The worker's last act is to queue its own "the thread stopped" notice. Flush it
        # here, while nothing more can arrive, so it cannot land after - and overwrite -
        # the final status below.
        self._drain_events()
        self._status("已停止")

    def _on_language_change(self) -> None:
        self.cfg.source_lang = LANG_CODE.get(self.src_var.get(), self.cfg.source_lang)
        self.cfg.target_lang = LANG_CODE.get(self.tgt_var.get(), self.cfg.target_lang)
        # Keep both combos consistent even when the change came from the other tab.
        self.src_var.set(LANG_LABEL.get(self.cfg.source_lang, self.cfg.source_lang))
        self.tgt_var.set(LANG_LABEL.get(self.cfg.target_lang, self.cfg.target_lang))
        self._push()
        self._report_engine_state()
        self._status("语言已切换")

    def swap_languages(self) -> None:
        self.cfg.source_lang, self.cfg.target_lang = (
            self.cfg.target_lang, self.cfg.source_lang)
        self.src_var.set(LANG_LABEL.get(self.cfg.source_lang, self.cfg.source_lang))
        self.tgt_var.set(LANG_LABEL.get(self.cfg.target_lang, self.cfg.target_lang))
        self._push()
        self._report_engine_state()

    def _on_engine_change(self) -> None:
        labels = engine_mod.engine_choices()
        chosen = self.engine_var.get()
        for key, label in labels.items():
            if label == chosen:
                self.cfg.translate.engine = key
                break
        self._push()
        # Warn once, and only when the engine itself was changed - a modal popping up
        # while someone switches language would be noise.
        engine = self.cfg.translate.engine
        if engine_mod.is_online(engine) and "online" not in self._warned:
            self._warned.add("online")
            messagebox.showwarning(
                "联网翻译会把屏幕文字发出去",
                "当前引擎是联网的：识别到的文字会发送给第三方翻译服务。\n\n"
                "涉及合同、报价、客户聊天这类内容时，请在「设置 → 翻译引擎」里"
                "换成「离线 · OPUS-MT」，那种模式下内容不出本机。\n\n"
                "免费接口还有每日字数限制，长时间使用容易中断。",
                parent=self)
        self._report_engine_state()

    def _report_engine_state(self) -> None:
        ready, detail = engine_mod.check(self.cfg, self.cfg.resolved_models_root())
        if not ready and detail:
            self._status(detail)

    def _on_overlay_toggle(self) -> None:
        self.cfg.overlay.enabled = bool(self.overlay_var.get())
        if not self.cfg.overlay.enabled:
            self.overlay.hide()
        self._apply_overlay_settings()
        self._push()

    def _apply_overlay_settings(self) -> None:
        if not self.cfg.overlay.enabled:
            self.overlay.hide()
            return
        self.overlay.apply_settings(self.cfg.overlay, self.cfg.region)
        want_exclude = self.cfg.overlay.position == "cover" and self.capture_exclude_var.get()
        ok = self.overlay.set_capture_excluded(want_exclude)
        if want_exclude and not ok and "exclude" not in self._warned:
            self._warned.add("exclude")
            messagebox.showwarning(
                "悬浮窗可能被自己翻译",
                "系统拒绝了「让悬浮窗对截屏隐身」这个设置，覆盖模式下程序可能"
                "会把自己的译文再翻译一遍。\n\n"
                "可以在「设置 → 字幕外观」里把位置改成「原文下方」来避开。",
                parent=self)

    def _push(self) -> None:
        if self.pipeline is not None:
            self.pipeline.update_config(self.cfg)

    def _status(self, message: str) -> None:
        self.status_var.set(message)

    # ------------------------------------------------------------ screen mode
    def _save(self) -> None:
        """Persist the config.

        The window-selection handlers used to call only _push(), so the selection lived in
        memory and was gone after a restart: "选完窗口重启就没了". Only the fixed-region path
        saved, which is why config.json on disk still said region mode while the running
        program was following a window.
        """
        try:
            config_mod.save(self.cfg)
        except Exception:  # noqa: BLE001
            pass

    def clear_region(self) -> None:
        """Forget the current selection, so Start refuses to run again.

        Picking the wrong window used to be one-way: the selection could be replaced but
        never removed, and the only way back to "nothing selected" was closing the
        program.
        """
        if self.pipeline is not None and self.pipeline.is_alive():
            messagebox.showinfo("还在跑", "先停止翻译，再清除选区。", parent=self)
            return
        self.cfg.target_mode = "region"
        self.cfg.region = Region()
        self.cfg.target_window_title = ""
        self.cfg.target_window_process = ""
        self.region_var.set(_pretty_region(self.cfg))
        self.overlay.hide()
        self._set_text(self.source_text, "")
        self._set_text(self.result_text, "")
        self._status("已清除选区")
        self.log("[选区] 已清除")
        try:
            config_mod.save(self.cfg)
        except Exception:  # noqa: BLE001
            pass

    def pick_window(self) -> None:
        """Choose the window to follow. This is the mode people actually want."""
        from .window_target import list_windows

        windows = [w for w in list_windows() if w.process_name.lower()
                   not in ("python.exe", "pythonw.exe") or "守译" not in w.title]
        if not windows:
            messagebox.showinfo("没有可用窗口", "没有找到可以翻译的窗口。", parent=self)
            return
        WindowPicker(self, windows, self._window_chosen)

    def _window_chosen(self, info) -> None:
        from .window_target import client_rect

        self.cfg.target_mode = "window"
        self.cfg.target_window_title = info.title
        self.cfg.target_window_process = info.process_name
        # Default the selection to the whole content area; the user can narrow it after.
        self.cfg.region = Region(left=0, top=0, width=info.client_width,
                                 height=info.client_height)
        self._sync_from_config()
        self._apply_overlay_settings()
        self._push()
        self._save()
        self._status(f"已选中窗口：{info.label}")
        self.log(f"目标窗口 = {info.title} ({info.process_name}) "
                 f"{info.client_width}x{info.client_height}")

    def frame_inside_window(self) -> None:
        """Draw the selection inside the chosen window, stored as a relative offset."""
        from .window_target import client_rect, find_window

        if self.cfg.target_mode != "window" or not self.cfg.target_window_title:
            messagebox.showinfo(
                "先选窗口",
                "「在窗口内框选」是相对窗口的位置，所以要先点「选择窗口」。\n\n"
                "如果只想固定翻译屏幕上的一块地方，用「框选屏幕区域」。",
                parent=self)
            return
        handle = find_window(self.cfg.target_window_title, self.cfg.target_window_process)
        client = client_rect(handle) if handle else None
        if client is None:
            messagebox.showwarning("窗口不可用", "目标窗口已经关闭或被最小化了。",
                                   parent=self)
            return
        self._pending_client = client
        self.overlay.hide()
        self.after(180, self._show_picker_for_window)

    def _show_picker_for_window(self) -> None:
        RegionPicker(self, self._region_inside_window).show()

    def _region_inside_window(self, region) -> None:
        if region is None:
            return
        client_x, client_y, client_width, client_height = self._pending_client
        left = max(0, region.left - client_x)
        top = max(0, region.top - client_y)
        width = min(region.width, client_width - left)
        height = min(region.height, client_height - top)
        if width < 8 or height < 8:
            messagebox.showwarning("框选无效", "框选的范围不在目标窗口里。", parent=self)
            return
        self.cfg.region = Region(left=left, top=top, width=width, height=height)
        self._sync_from_config()
        self._apply_overlay_settings()
        self._push()
        self._save()
        self._status("窗口内的范围已更新，可以开始翻译了")

    def pick_region(self) -> None:
        self.cfg.target_mode = "region"
        self.overlay.hide()
        self.after(180, self._show_picker)

    def _show_picker(self) -> None:
        RegionPicker(self, self._region_chosen).show()

    def _region_chosen(self, region) -> None:
        if region is None:
            return
        self.cfg.target_mode = "region"
        self.cfg.region = region
        self.region_var.set(_pretty_region(self.cfg))
        self._apply_overlay_settings()
        self._push()
        self._status("区域已更新，可以开始翻译了")
        try:
            config_mod.save(self.cfg)
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------- text mode
    def run_text_translation(self) -> None:
        if self._text_thread is not None and self._text_thread.is_alive():
            self._text_stop.set()
            return
        text = self.text_in.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("没有内容", "请先粘贴或输入要翻译的文字。", parent=self)
            return
        ready, detail = engine_mod.check(self.cfg, self.cfg.resolved_models_root())
        if not ready:
            messagebox.showwarning("翻译引擎不可用", detail, parent=self)
            return

        self._text_stop.clear()
        self.text_run_btn.configure(text="停止")
        self._set_text(self.text_out, "")
        self._status("正在翻译…")

        def worker() -> None:
            try:
                result = translate_text(
                    self.cfg, self.cfg.resolved_models_root(), text,
                    on_progress=self._on_text_progress,
                    should_stop=self._text_stop.is_set,
                    mode=self._text_mode)
                self.after(0, lambda: self._finish_text(result))
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc()
                self.after(0, lambda: self._fail_text(exc, detail))

        self._text_thread = threading.Thread(target=worker, name="text-translate",
                                             daemon=True)
        self._text_thread.start()

    def _on_text_progress(self, done: int, total: int) -> None:
        self.after(0, lambda: self._status(f"正在翻译… {done}/{total}"))

    def _finish_text(self, result) -> None:
        self._set_text(self.text_out, result.translation)
        self.text_run_btn.configure(text="翻译")
        if getattr(result, "mode", "line") == "paragraph":
            done = f"{result.lines} 段 / {result.sentences} 句"
        else:
            done = f"{result.sentences} 行"
        if result.failed:
            self._status(f"完成 {done}，{len(result.failed)} 处失败"
                         f"（{result.elapsed:.1f} 秒）")
        else:
            self._status(f"完成 {done}，用时 {result.elapsed:.1f} 秒")

        # Say what was hidden and, more importantly, what did not come back. A placeholder
        # left in the text is the one failure the customer must not be allowed to miss.
        redacted = getattr(result, "redacted", 0)
        unrestored = getattr(result, "unrestored", []) or []
        if unrestored:
            self._status(f"⚠ 有 {len(unrestored)} 处敏感信息没能还原："
                         f"{'、'.join(unrestored[:4])}"
                         f"{' 等' if len(unrestored) > 4 else ''} —— 请核对译文")
            self.log(f"[脱敏] 未还原 {unrestored}")
        elif redacted:
            self._status(f"（已遮住 {redacted} 处敏感信息，翻完已还原）")

        # A number the engine ate is the one loss a reader cannot spot for themselves: the
        # sentence still reads as a sentence, it is just missing a quantity.
        lost_numbers = getattr(result, "numbers_unrestored", []) or []
        if lost_numbers:
            self._status(f"⚠ 有 {len(lost_numbers)} 个数字没能还原："
                         f"{'、'.join(lost_numbers[:4])}——请核对原文")
            self.log(f"[数字] 未还原 {lost_numbers}")

    def _fail_text(self, exc: BaseException, detail: str) -> None:
        self.text_run_btn.configure(text="翻译")
        self._status(f"翻译失败：{exc}")
        self.log(f"[文本翻译失败] {exc}\n{detail}")

    # ------------------------------------------------------------ word document
    def translate_document(self) -> None:
        """Translate a Word file or a spreadsheet whole, keeping its layout.

        A contract arrives as a .docx and a price list usually as an .xlsx, and retyping
        either into the panes is the work this tab exists to avoid. Neither is pasted in:
        each is translated whole and written back out with everything but the text left
        alone. See app/docx_io.py and app/xlsx_io.py for what is traded away.
        """
        if self._text_thread is not None and self._text_thread.is_alive():
            messagebox.showinfo("还在忙", "上一个翻译还没结束，等它跑完。", parent=self)
            return
        source = filedialog.askopenfilename(
            parent=self, title="选择要翻译的文档",
            filetypes=[("Word 和 Excel", "*.docx *.xlsx"),
                       ("Word 文档", "*.docx"),
                       ("Excel 表格", "*.xlsx"),
                       ("所有文件", "*.*")])
        if not source:
            return
        suffix = Path(source).suffix.lower()
        handlers = {
            ".docx": ("Word 文档", translate_docx),
            ".xlsx": ("Excel 表格", translate_xlsx),
        }
        if suffix not in handlers:
            messagebox.showinfo(
                "这个格式还翻译不了",
                f"整篇翻译目前支持 .docx（Word）和 .xlsx（Excel）。\n"
                f"你选的是 {suffix or '没有扩展名的文件'}。\n\n"
                "纯文本、.csv、.srt 这些请用旁边的「导入文件」。",
                parent=self)
            return
        label, translate_fn = handlers[suffix]
        ready, detail = engine_mod.check(self.cfg, self.cfg.resolved_models_root())
        if not ready:
            messagebox.showwarning("翻译引擎不可用", detail, parent=self)
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="译文保存到", defaultextension=suffix,
            initialfile=f"{Path(source).stem}_译文{suffix}",
            filetypes=[(label, f"*{suffix}")])
        if not target:
            return

        self._text_stop.clear()
        self.text_run_btn.configure(text="停止")
        self._status(f"正在翻译{label} {Path(source).name}…")

        def worker() -> None:
            try:
                result = translate_fn(
                    self.cfg, self.cfg.resolved_models_root(), source, target,
                    on_progress=self._on_text_progress,
                    should_stop=self._text_stop.is_set)
                self.after(0, lambda: self._finish_document(result, target, suffix))
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc()
                self.after(0, lambda: self._fail_text(exc, detail))

        self._text_thread = threading.Thread(target=worker, name="document-translate",
                                             daemon=True)
        self._text_thread.start()

    def _finish_document(self, result, target: str, suffix: str) -> None:
        self.text_run_btn.configure(text="翻译")
        failed = f"，{len(result.failed)} 处失败" if result.failed else ""
        # The two translators count different things, and either number alone is
        # meaningless to the person waiting: a spreadsheet has strings and cells, a
        # document has paragraphs and sentences.
        if suffix == ".xlsx":
            what = f"{result.strings} 条文字（{result.cells} 个单元格）"
        else:
            what = f"{result.paragraphs} 段 / {result.sentences} 句"
        masked = f"，已遮住 {result.redacted} 处敏感信息" if getattr(
            result, "redacted", 0) else ""
        self._status(f"文档已翻译：{what}{failed}{masked}，"
                     f"用时 {result.elapsed:.1f} 秒 → {Path(target).name}")
        self.log(f"[文档翻译] {target}")
        if getattr(result, "unrestored", None):
            self._status(f"⚠ 有 {len(result.unrestored)} 处敏感信息没能还原，请核对译文")
            self.log(f"[文档翻译·脱敏] 未还原 {result.unrestored}")

    # ----------------------------------------------------------------- picture
    def translate_picture(self) -> None:
        """Translate the text out of a picture: a contract photo, a screenshot, a scan.

        A trading desk gets these far more often than documents - a counterparty
        photographs a signed page and sends it over WeChat - and there is nothing to
        paste into the panes. Recognition is the same engine the screen tab uses, so
        numbers survive the same way they do there.
        """
        if self._text_thread is not None and self._text_thread.is_alive():
            messagebox.showinfo("还在忙", "上一个翻译还没结束，等它跑完。", parent=self)
            return
        patterns = " ".join("*" + suffix for suffix in IMAGE_SUFFIXES)
        source = filedialog.askopenfilename(
            parent=self, title="选择要翻译的图片",
            filetypes=[("图片", patterns), ("所有文件", "*.*")])
        if not source:
            return
        ready, detail = engine_mod.check(self.cfg, self.cfg.resolved_models_root())
        if not ready:
            messagebox.showwarning("翻译引擎不可用", detail, parent=self)
            return

        self._text_stop.clear()
        self.text_run_btn.configure(text="停止")
        self._set_text(self.text_in, "")
        self._set_text(self.text_out, "")
        self._status("正在识别 " + Path(source).name + "…")

        def worker() -> None:
            try:
                result = translate_image(
                    self.cfg, self.cfg.resolved_models_root(), source,
                    on_progress=self._on_text_progress,
                    should_stop=self._text_stop.is_set)
                self.after(0, lambda: self._finish_picture(result, source))
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc()
                self.after(0, lambda: self._fail_text(exc, detail))

        self._text_thread = threading.Thread(target=worker, name="picture-translate",
                                             daemon=True)
        self._text_thread.start()

    def _finish_picture(self, result, source: str) -> None:
        self.text_run_btn.configure(text="翻译")
        if result.empty:
            self._status(Path(source).name + " 里没认出字——换张清楚点的，"
                         "或者到「设置 → 识别与高级」换个识别模型")
            self.log("[图片] 没认出文字：" + source)
            return
        # Both halves go into the panes that already exist: the left one is what the
        # picture says, the right one is what it means. 复制译文 and 保存译文 then work
        # on them without a second set of buttons.
        self._set_text(self.text_in, result.original_text)
        self._set_text(self.text_out, result.translated_text)
        self._text_mode = "paragraph"
        failed = "，" + str(result.failed) + " 处失败" if result.failed else ""
        scaled = "，图太大已缩小" if result.scaled else ""
        self._status("图片已翻译：" + str(result.paragraphs) + " 段" + failed
                     + "，识别 " + f"{result.ocr_seconds:.1f}" + " 秒 / 共 "
                     + f"{result.elapsed:.1f}" + " 秒" + scaled)
        self.log("[图片翻译] " + source)
        if result.unrestored:
            self._status("⚠ 有 " + str(len(result.unrestored))
                         + " 处敏感信息没能还原，请核对译文")
            self.log("[图片·脱敏] 未还原 " + str(result.unrestored))
        if result.numbers_unrestored:
            shown = "、".join(result.numbers_unrestored[:4])
            self._status("⚠ 有 " + str(len(result.numbers_unrestored))
                         + " 个数字没能还原：" + shown + "——请核对原文")
            self.log("[图片·数字] 未还原 " + str(result.numbers_unrestored))

    # ------------------------------------------------------------------- pdf
    def translate_pdf(self) -> None:
        """Translate a PDF page by page, whether its pages carry text or pictures.

        A contract arrives as a PDF and nothing else, and half of them are scans. Each
        page decides for itself: a digital page is read exactly, a scanned one is
        recognised with the same engine the screen tab uses.
        """
        if self._text_thread is not None and self._text_thread.is_alive():
            messagebox.showinfo("还在忙", "上一个翻译还没结束，等它跑完。", parent=self)
            return
        patterns = " ".join("*" + suffix for suffix in PDF_SUFFIXES)
        source = filedialog.askopenfilename(
            parent=self, title="选择要翻译的 PDF",
            filetypes=[("PDF", patterns), ("所有文件", "*.*")])
        if not source:
            return
        ready, detail = engine_mod.check(self.cfg, self.cfg.resolved_models_root())
        if not ready:
            messagebox.showwarning("翻译引擎不可用", detail, parent=self)
            return

        self._text_stop.clear()
        self.text_run_btn.configure(text="停止")
        self._set_text(self.text_in, "")
        self._set_text(self.text_out, "")
        self._status("正在打开 " + Path(source).name + "…")

        def worker() -> None:
            try:
                result = translate_pdf(
                    self.cfg, self.cfg.resolved_models_root(), source,
                    on_progress=self._on_page_progress,
                    should_stop=self._text_stop.is_set)
                self.after(0, lambda: self._finish_pdf(result, source))
            except PdfError as exc:
                self.after(0, lambda: self._fail_pdf(exc))
            except Exception as exc:  # noqa: BLE001
                detail = traceback.format_exc()
                self.after(0, lambda: self._fail_text(exc, detail))

        self._text_thread = threading.Thread(target=worker, name="pdf-translate",
                                             daemon=True)
        self._text_thread.start()

    def _on_page_progress(self, done: int, total: int) -> None:
        self.after(0, lambda: self._status(f"正在翻译 PDF… 第 {done}/{total} 页"))

    def _fail_pdf(self, exc: BaseException) -> None:
        self.text_run_btn.configure(text="翻译")
        self._status(str(exc))
        self.log("[PDF] " + str(exc))

    def _finish_pdf(self, result, source: str) -> None:
        self.text_run_btn.configure(text="翻译")
        if result.empty:
            self._status(Path(source).name + " 里没认出字——可能是纯图片扫描件，"
                         "或者识别模型没装")
            self.log("[PDF] 没读出文字：" + source)
            return
        self._set_text(self.text_in, result.original_text)
        self._set_text(self.text_out, result.translated_text)
        self._text_mode = "paragraph"
        scanned = (f"，其中 {result.scanned_pages} 页是扫描件（靠识别）"
                   if result.scanned_pages else "")
        failed = f"，{result.failed} 处失败" if result.failed else ""
        self._status(f"PDF 已翻译：{result.total_pages} 页{scanned}{failed}，"
                     f"用时 {result.elapsed:.1f} 秒")
        self.log("[PDF 翻译] " + source)
        if result.unrestored:
            self._status("⚠ 有 " + str(len(result.unrestored))
                         + " 处敏感信息没能还原，请核对译文")
            self.log("[PDF·脱敏] 未还原 " + str(result.unrestored))
        if result.numbers_unrestored:
            shown = "、".join(result.numbers_unrestored[:4])
            self._status("⚠ 有 " + str(len(result.numbers_unrestored))
                         + " 个数字没能还原：" + shown + "——请核对原文")
            self.log("[PDF·数字] 未还原 " + str(result.numbers_unrestored))

    def clear_text(self) -> None:
        self.text_in.delete("1.0", "end")
        self._set_text(self.text_out, "")
        self._text_mode = "auto"
        self._status("已清空")

    def copy_translation(self) -> None:
        value = self.text_out.get("1.0", "end").strip()
        if not value:
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self._status("译文已复制")

    def import_file(self) -> None:
        patterns = " ".join(f"*{suffix}" for suffix in TEXT_SUFFIXES)
        path = filedialog.askopenfilename(
            parent=self, title="选择要翻译的文本文件",
            filetypes=[("文本文件", patterns), ("所有文件", "*.*")])
        if not path:
            return
        try:
            content = read_text_file(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("读取失败", str(exc), parent=self)
            return
        self.text_in.delete("1.0", "end")
        self.text_in.insert("1.0", content)
        self._text_mode = "line" if Path(path).suffix.lower() in STRUCTURED_SUFFIXES \
            else "auto"
        self._status(f"已导入 {Path(path).name}（{len(content)} 字符）")

    def save_translation(self) -> None:
        value = self.text_out.get("1.0", "end").strip()
        if not value:
            messagebox.showinfo("还没有译文", "先翻译出结果再保存。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="保存译文", defaultextension=".txt",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            Path(path).write_text(value, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("保存失败", str(exc), parent=self)
            return
        self._status(f"译文已保存到 {path}")

    # ---------------------------------------------------------------- hotkeys
    def _start_hotkeys(self) -> None:
        mapping = {
            "start": self.cfg.hotkeys.start,
            "stop": self.cfg.hotkeys.stop,
            "pause": getattr(self.cfg.hotkeys, "pause", "F11"),
            "pick": self.cfg.hotkeys.pick_region,
        }
        try:
            self.hotkeys = HotkeyManager(mapping, self.hotkey_queue)
            self.hotkeys.start()
            self.hotkeys.wait_ready(2.0)
            for name, reason in self.hotkeys.failed.items():
                self.log(f"[热键] {name} 注册失败：{reason}")
        except Exception as exc:  # noqa: BLE001
            self.hotkeys = None
            self.log(f"[热键] 无法启动：{exc}")

    # ------------------------------------------------------------ event pump
    def _drain_events(self) -> None:
        """Handle everything the worker has queued so far.

        Split out of _drain so the stop path can flush the queue without scheduling
        another pump tick - two self-rescheduling pumps would multiply.
        """
        try:
            while True:
                self._handle_event(self.events.get_nowait())
        except queue.Empty:
            pass

    def _drain(self) -> None:
        self._drain_events()
        try:
            while True:
                name = self.hotkey_queue.get_nowait()
                if name == "start":
                    self.start()
                elif name == "stop":
                    self.stop()
                elif name == "pause":
                    self.toggle_pause()
                elif name == "pick":
                    self.pick_region()
        except queue.Empty:
            pass
        self.after(60, self._drain)

    def _handle_event(self, event: Event) -> None:
        if event.kind == TARGET:
            # The capture rectangle moved, so the overlay must move with it - this is
            # what keeps the subtitle sitting on the window after it is dragged.
            target = event.meta.get("region") if event.meta else None
            if target:
                region = Region(left=target[0], top=target[1],
                                width=target[2], height=target[3])
                # Only the overlay gets the absolute screen rectangle. Writing it back
                # into cfg.region was the bug that broke window following outright: in
                # window mode cfg.region is the *offset inside the window*, so storing
                # screen coordinates there made the next frame clamp the capture to
                # (client_width - offset) - 1202 - 664 = 538, then smaller - until it hit
                # zero and the UI said "目标窗口不可用" with the window plainly on screen.
                if getattr(self.cfg, "target_mode", "region") != "window":
                    self.cfg.region = region
                self.region_var.set(_pretty_region(self.cfg))
                if self.cfg.overlay.enabled:
                    self.overlay.apply_settings(self.cfg.overlay, region)
            return
        if event.kind == RESULT:
            if event.source:
                self._set_text(self.source_text, event.source)
            if event.changed:
                # Recorded because a GUI session used to leave nothing behind: the on-screen
                # translation is the only evidence, and "why does it say that" was unanswerable
                # afterwards without the source text, the output and the timings side by side.
                self.log(f"[译文] 识别 {event.ocr_ms:.0f} ms 翻译 {event.translate_ms:.0f} ms")
                self.log(f"       原文: {event.source[:200]!r}")
                self.log(f"       译文: {event.translation[:200]!r}")
                self._set_text(self.result_text, event.translation)
                if self.cfg.overlay.enabled:
                    # Positioned rendering when the pipeline paired every translation with
                    # its box; the flat panel stays as the fallback, because a wrong
                    # position is worse than no position.
                    items = (event.meta or {}).get("items") or []
                    placed = False
                    if items:
                        try:
                            self.overlay.show_items(items, self.overlay._region)
                            placed = True
                        except Exception:  # noqa: BLE001
                            placed = False
                    if not placed:
                        self.overlay.show_text(event.source, event.translation)
                if self.cfg.copy_to_clipboard and event.translation:
                    try:
                        self.clipboard_clear()
                        self.clipboard_append(event.translation)
                    except Exception:  # noqa: BLE001
                        pass
            elif not event.source:
                self.overlay.hide()
                self._set_text(self.source_text, "")
        elif event.kind == STATS:
            if event.ocr_ms or event.translate_ms:
                self.timing_var.set(
                    f"识别 {event.ocr_ms:.0f} ms · 翻译 {event.translate_ms:.0f} ms")
        elif event.kind == STATUS:
            self._status(event.message)
            if self._last_status != event.message:
                self._last_status = event.message
                self.log(event.message)
        elif event.kind == ERROR:
            self._status(event.message)
            self.log(f"[错误] {event.message}")
            detail = event.meta.get("detail")
            if detail:
                self.log(detail.strip().splitlines()[-1])

    # ------------------------------------------------------------------- log
    def log(self, message: str) -> None:
        """Record a line, to the settings window when it is open and always to logs/app.log.

        The view used to be the only destination, so closing that window threw the record
        away - a GUI session left nothing behind to look at afterwards, which is why an
        unexplained on-screen result could not be diagnosed at all. The file is capped and
        rotated so it cannot grow without limit.
        """
        window = getattr(self, "_settings_window", None)
        for line in str(message).splitlines() or [""]:
            self._log_to_window(line)
        try:
            self._log_to_file(str(message))
        except Exception:  # noqa: BLE001
            pass

    def _log_to_window(self, line: str) -> None:
        window = getattr(self, "_settings_window", None)
        try:
            if window is not None and window.winfo_exists():
                window.append_log(line)
        except Exception:  # noqa: BLE001
            pass

    def _log_to_file(self, message: str) -> None:
        import time as _time
        from . import config as _config

        _config.LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _config.LOG_DIR / "app.log"
        # Rotate at a size a support conversation can still be shown in full.
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            backup = _config.LOG_DIR / "app.log.1"
            try:
                if backup.exists():
                    backup.unlink()
                path.rename(backup)
            except Exception:  # noqa: BLE001
                pass
        stamp = _time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8", errors="replace") as handle:
            for line in str(message).splitlines() or [""]:
                handle.write(f"{stamp}  {line}\n")

    # -------------------------------------------------------------- downloads
    def download_models(self) -> None:
        """Fetch whatever the selected engine still needs, off the UI thread."""
        from . import downloader, models as model_registry

        engine = self.cfg.translate.engine
        spec = engine_mod.catalog().get(engine)
        if spec is None or spec.kind != "local":
            self._status("当前引擎不需要下载模型")
            return
        ready, detail = engine_mod.check(self.cfg, self.cfg.resolved_models_root())
        if ready:
            self._status("模型已经就绪，不需要下载")
            return

        src, tgt = self.cfg.source_lang, self.cfg.target_lang
        root = self.cfg.resolved_models_root()
        mirror = self.cfg.mirror
        is_nllb = engine == "nllb"

        def progress(done: int, total: int) -> None:
            if total:
                self.after(0, lambda: self._status(
                    f"正在下载模型… {done / 1048576:.1f} / {total / 1048576:.1f} MB"))

        def worker() -> None:
            try:
                if is_nllb:
                    key = "nllb-200-distilled-600M"
                    downloader.download_multilingual(root, key, mirror=mirror,
                                                     on_progress=progress)
                else:
                    downloader.download_route(root, src, tgt, mirror=mirror,
                                              on_progress=progress)
                self.after(0, lambda: self._status("模型下载完成"))
                self.after(0, self._refresh_after_download)
            except Exception as exc:  # noqa: BLE001
                detail_text = traceback.format_exc()
                self.after(0, lambda: self._status(f"模型下载失败：{exc}"))
                self.after(0, lambda: self.log(f"[下载失败] {exc}\n{detail_text}"))

        self._status("开始下载模型…")
        threading.Thread(target=worker, name="model-download", daemon=True).start()

    def _refresh_after_download(self) -> None:
        window = getattr(self, "_settings_window", None)
        if window is not None and window.winfo_exists():
            try:
                window._refresh_model_state()
            except Exception:  # noqa: BLE001
                pass
        self._report_engine_state()

    # -------------------------------------------------------------- lifecycle
    def _on_close(self) -> None:
        self._text_stop.set()
        try:
            config_mod.save(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        self.stop()
        if self.hotkeys is not None:
            try:
                self.hotkeys.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            self.overlay.destroy()
        except Exception:  # noqa: BLE001
            pass
        self.destroy()


class SettingsWindow(tk.Toplevel):
    """Everything that is not used every day, in one place."""

    def __init__(self, app: App) -> None:
        super().__init__(app)
        self.app = app
        self.cfg = app.cfg
        # Before any tab is built: constructing a tab logs, and logging writes here.
        self._log_buffer: list[str] = []
        self.title("设置")
        self.configure(bg=BG)
        self.geometry("680x560")
        self.transient(app)
        app._settings_window = self

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=16, pady=16)
        notebook.add(self._tab_engine(notebook), text="翻译引擎")
        notebook.add(self._tab_glossary(notebook), text="词库")
        notebook.add(self._tab_overlay(notebook), text="字幕外观")
        notebook.add(self._tab_advanced(notebook), text="识别与高级")
        notebook.add(self._tab_log(notebook), text="日志")
        notebook.add(self._tab_about(notebook), text="关于")

        footer = ttk.Frame(self, padding=(16, 0, 16, 14))
        footer.pack(fill="x")
        ttk.Button(footer, text="关闭", command=self._close).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _close(self) -> None:
        try:
            config_mod.save(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        self.app._push()
        self.destroy()

    # -------------------------------------------------------------- engine
    def _tab_engine(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        engine = self.cfg.translate.engine

        spec = engine_mod.catalog().get(engine)
        ttk.Label(tab, text=engine_mod.engine_label(engine),
                  font=TITLE_FONT).pack(anchor="w")
        if spec and spec.hint:
            ttk.Label(tab, text=spec.hint, style="Muted.TLabel",
                      wraplength=600, justify="left").pack(anchor="w", pady=(4, 12))

        if spec and spec.kind in ("online", "http"):
            ttk.Label(tab,
                      text="⚠ 这个引擎会把识别到的文字发送到第三方。"
                           "处理合同、报价等敏感内容请改用离线引擎。",
                      style="Warn.TLabel", wraplength=600,
                      justify="left").pack(anchor="w", pady=(0, 8))

            # The mask is on by default and its whole point is to be believed, so it is
            # shown rather than buried: the customer needs to see the switch, and needs to
            # know the masked parts come back in the translation.
            self.redact_var = tk.BooleanVar(
                value=bool(getattr(self.cfg, "redact_enabled", True)))
            ttk.Checkbutton(
                tab, text="发送前遮住敏感信息（公司名、税号、银行账号、电话、邮箱、网址）",
                variable=self.redact_var,
                command=self._on_redact_toggle).pack(anchor="w")
            ttk.Label(tab,
                      text="遮掉的地方翻完会自动还原，你在译文里看到的还是原来的公司名和号码。"
                           "价格、日期、数量不遮。",
                      style="Muted.TLabel", wraplength=600,
                      justify="left").pack(anchor="w", pady=(0, 12))

        # Baidu credentials
        if engine == "baidu":
            form = ttk.Frame(tab)
            form.pack(fill="x", pady=(0, 8))
            self.baidu_appid_var = tk.StringVar(value=self.cfg.baidu_appid)
            self.baidu_key_var = tk.StringVar(value=self.cfg.baidu_key)
            for row, (label, var, show) in enumerate(
                    (("appid", self.baidu_appid_var, ""), ("密钥", self.baidu_key_var, "•"))):
                ttk.Label(form, text=label, style="Muted.TLabel").grid(
                    row=row, column=0, sticky="w", pady=4)
                ttk.Entry(form, textvariable=var, width=44, show=show).grid(
                    row=row, column=1, sticky="ew", pady=4, padx=(10, 0))
                var.trace_add("write", lambda *_: self._save_credentials())
            form.columnconfigure(1, weight=1)
            ttk.Label(tab, text="到 fanyi-api.baidu.com 申请；标准版每月免费 5 万字符。",
                      style="Muted.TLabel").pack(anchor="w", pady=(0, 12))

        # OpenAI-compatible endpoint
        if engine == "openai-compatible":
            form = ttk.Frame(tab)
            form.pack(fill="x", pady=(0, 8))
            self.openai_url_var = tk.StringVar(value=self.cfg.openai_base_url)
            self.openai_model_var = tk.StringVar(value=self.cfg.openai_model)
            self.openai_key_var = tk.StringVar(value=self.cfg.openai_api_key)
            self.openai_prompt_var = tk.StringVar(value=self.cfg.openai_prompt)
            self.openai_timeout_var = tk.StringVar(
                value=f"{self.cfg.openai_timeout:g}")
            fields = (("接口地址", self.openai_url_var, ""),
                      ("模型名", self.openai_model_var, ""),
                      ("API Key", self.openai_key_var, "•"),
                      ("提示词", self.openai_prompt_var, ""),
                      ("超时(秒)", self.openai_timeout_var, ""))
            for row, (label, var, show) in enumerate(fields):
                ttk.Label(form, text=label, style="Muted.TLabel").grid(
                    row=row, column=0, sticky="w", pady=4)
                ttk.Entry(form, textvariable=var, width=44, show=show).grid(
                    row=row, column=1, sticky="ew", pady=4, padx=(10, 0))
                var.trace_add("write", lambda *_: self._save_openai())
            form.columnconfigure(1, weight=1)

            # Without this the only way to find out that the key is wrong, or that the
            # address is missing /v1, is to start a translation and read an error.
            row = ttk.Frame(tab)
            row.pack(fill="x", pady=(0, 8))
            self.openai_test_btn = ttk.Button(row, text="测试连接",
                                              command=self._test_openai)
            self.openai_test_btn.pack(side="left")
            self.openai_state_var = tk.StringVar()
            ttk.Label(row, textvariable=self.openai_state_var, style="Muted.TLabel",
                      wraplength=430, justify="left").pack(side="left", padx=10)

            ttk.Label(tab, text="任何 OpenAI 兼容接口都行：DeepSeek、Moonshot、"
                                "本地 Ollama / LM Studio。\n"
                                "接口地址填到 /v1 为止，例如 https://api.deepseek.com/v1 、"
                                "http://localhost:11434/v1；\n"
                                "只填域名也行，会自动补 /v1。本地服务的 API Key 留空。\n"
                                "提示词留空用默认那句；可以写 {src} {tgt} 当占位符。",
                      style="Muted.TLabel", wraplength=600,
                      justify="left").pack(anchor="w", pady=(0, 12))

        # Offline model management
        if spec and spec.kind == "local":
            box = ttk.Frame(tab)
            box.pack(fill="x", pady=(0, 12))
            self.model_state_var = tk.StringVar()
            ttk.Label(box, textvariable=self.model_state_var,
                      style="Muted.TLabel", wraplength=600).pack(anchor="w", pady=(0, 8))
            self.download_btn = ttk.Button(box, text="下载模型", command=self._download)
            self.download_btn.pack(anchor="w")

        ttk.Separator(tab, orient="horizontal").pack(fill="x", pady=12)
        ttk.Label(tab, text="自己接模型（插件）", font=TITLE_FONT).pack(anchor="w")
        ttk.Label(tab, text="把 .py 放进插件目录即可出现在引擎列表里，"
                            "格式见目录中的 README.md。升级程序不会覆盖它。",
                  style="Muted.TLabel", wraplength=600,
                  justify="left").pack(anchor="w", pady=(4, 8))
        row = ttk.Frame(tab)
        row.pack(anchor="w")
        ttk.Button(row, text="重新扫描", command=self._reload_plugins).pack(side="left")
        ttk.Button(row, text="打开插件目录", command=self._open_plugins).pack(
            side="left", padx=8)
        self.plugin_state_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.plugin_state_var, style="Muted.TLabel",
                  wraplength=600, justify="left").pack(anchor="w", pady=(8, 0))
        self._refresh_plugins()
        if spec and spec.kind == "local":
            self._refresh_model_state()
        return tab

    def _save_credentials(self) -> None:
        self.cfg.baidu_appid = self.baidu_appid_var.get().strip()
        self.cfg.baidu_key = self.baidu_key_var.get().strip()
        self.app._push()

    def _save_openai(self) -> None:
        self.cfg.openai_base_url = self.openai_url_var.get().strip()
        self.cfg.openai_model = self.openai_model_var.get().strip()
        self.cfg.openai_api_key = self.openai_key_var.get().strip()
        self.cfg.openai_prompt = self.openai_prompt_var.get().strip()
        try:
            timeout = float(self.openai_timeout_var.get().strip() or 60)
        except ValueError:
            timeout = 60.0
        # A zero or negative timeout would make urllib fail instantly on every request,
        # which looks like the endpoint is down.
        self.cfg.openai_timeout = timeout if timeout >= 5 else 60.0
        self.app._push()

    def _test_openai(self) -> None:
        """Ask the endpoint to translate one word, and report what came back.

        Configuration mistakes here are invisible until a translation fails, and then
        the message arrives in the middle of real work. This makes the failure happen
        on purpose, in the settings window, where the two fields that caused it are.
        """
        base = self.openai_url_var.get().strip()
        model = self.openai_model_var.get().strip()
        if not base or not model:
            self.openai_state_var.set("先填接口地址和模型名")
            return
        self._save_openai()
        self.openai_state_var.set("正在连接…")
        self.openai_test_btn.configure(state="disabled")

        def worker() -> None:
            from .engines.http_engine import endpoint_of, OpenAIChatTranslator

            try:
                tester = OpenAIChatTranslator(self.cfg, "ru", "zh", self.cfg.translate)
                tester.load()
                # A word, not a sentence: the point is to prove the connection, and a
                # real sentence would cost real tokens on a paid endpoint.
                out = tester.translate("Москва")
                self.after(0, lambda: self._test_openai_done(
                    True, f"通了。{endpoint_of(base)} 回的是「{out[:40]}」"))
            except Exception as exc:  # noqa: BLE001
                self.after(0, lambda: self._test_openai_done(False, str(exc)))

        threading.Thread(target=worker, name="openai-test", daemon=True).start()

    def _test_openai_done(self, ok: bool, message: str) -> None:
        self.openai_test_btn.configure(state="normal")
        self.openai_state_var.set(("✓ " if ok else "✗ ") + message)

    def _reload_plugins(self) -> None:
        engine_mod.reload()
        labels = engine_mod.engine_choices()
        self.app.engine_box.configure(values=list(labels.values()))
        self.app._sync_from_config()
        self._refresh_plugins()

    def _open_plugins(self) -> None:
        try:
            os.startfile(engine_mod.plugins_dir())  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("打不开目录", str(exc), parent=self)

    def _refresh_plugins(self) -> None:
        specs = engine_mod.catalog()
        custom = [s for s in specs.values() if s.kind == "custom"]
        parts = [f"共 {len(specs)} 个引擎，其中自定义 {len(custom)} 个"]
        if custom:
            parts.append("：" + "、".join(s.label for s in custom))
        for problem in engine_mod.problems()[:3]:
            parts.append(f"\n加载问题：{Path(problem.path).name}: {problem.message}")
        self.plugin_state_var.set("".join(parts))

    def _refresh_model_state(self) -> None:
        ready, detail = engine_mod.check(self.cfg, self.cfg.resolved_models_root())
        self.model_state_var.set(detail or "")
        self.download_btn.state(["disabled"] if ready else ["!disabled"])

    def _download(self) -> None:
        self.download_btn.state(["disabled"])
        self.app.download_models()
    # ------------------------------------------------------------- glossary
    def _tab_glossary(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        ttk.Label(tab, text="术语表（词库）", font=TITLE_FONT).pack(anchor="w")
        ttk.Label(tab,
                  text="强制指定人名、商品名、专业词的译法。机器翻译不认识这些，"
                       "词库里写死就准了。改完下一句就生效，不用重启。",
                  style="Muted.TLabel", wraplength=600,
                  justify="left").pack(anchor="w", pady=(4, 12))

        self.glossary_var = tk.BooleanVar(value=self.cfg.glossary_enabled)
        ttk.Checkbutton(tab, text="启用词库", variable=self.glossary_var,
                        command=self._on_glossary_toggle).pack(anchor="w")

        add = ttk.Frame(tab)
        add.pack(fill="x", pady=12)
        ttk.Label(add, text="原文", style="Muted.TLabel").pack(side="left")
        self.rule_src_var = tk.StringVar()
        ttk.Entry(add, textvariable=self.rule_src_var, width=18).pack(side="left", padx=6)
        ttk.Label(add, text="→ 译文", style="Muted.TLabel").pack(side="left")
        self.rule_dst_var = tk.StringVar()
        ttk.Entry(add, textvariable=self.rule_dst_var, width=18).pack(side="left", padx=6)
        ttk.Button(add, text="添加", command=self._add_rule).pack(side="left", padx=4)

        row = ttk.Frame(tab)
        row.pack(anchor="w")
        ttk.Button(row, text="打开词库文件", command=self._open_glossary).pack(side="left")
        ttk.Button(row, text="重新加载", command=self._refresh_glossary).pack(
            side="left", padx=8)
        ttk.Button(row, text="导入词表", command=self._import_glossary).pack(side="left")
        ttk.Button(row, text="导出 TSV", command=self._export_glossary).pack(
            side="left", padx=8)

        self.glossary_state_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.glossary_state_var, style="Muted.TLabel",
                  wraplength=600, justify="left").pack(anchor="w", pady=(12, 6))

        # A few hundred built-in terms plus the user's own is past the point where
        # scrolling is a way to find one.
        self.glossary_filter_var = tk.StringVar()
        self.glossary_filter_var.trace_add("write",
                                           lambda *_: self._refresh_glossary())
        search = ttk.Frame(tab)
        search.pack(fill="x", pady=(0, 6))
        ttk.Label(search, text="筛选", style="Muted.TLabel").pack(side="left")
        ttk.Entry(search, textvariable=self.glossary_filter_var, width=20).pack(
            side="left", padx=6)
        ttk.Label(search, text="输入几个字，只留下包含它的规则",
                  style="Muted.TLabel").pack(side="left")

        # The rules themselves. Being able to add a term but not see or remove it is
        # not a glossary a business can run on - a few hundred terms would only be
        # editable by hand in the JSON file.
        table_frame = ttk.Frame(tab)
        table_frame.pack(fill="both", expand=True)
        # The condition and the note are both shown. A rule reading 顺序 -> 订单 looks
        # like a mistake until you can see that it only fires when the source says заказ,
        # and the note is where a rule records which engine mistake it came from.
        columns = ("src", "dst", "pairs", "when", "note", "origin")
        self.rule_table = ttk.Treeview(table_frame, columns=columns, show="headings",
                                       height=8, selectmode="extended")
        for key, title, width in (("src", "原文", 115), ("dst", "译文", 115),
                                  ("pairs", "适用", 70), ("when", "生效条件", 125),
                                  ("note", "说明", 215), ("origin", "来源", 55)):
            self.rule_table.heading(key, text=title)
            self.rule_table.column(key, width=width, anchor="w")
        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=self.rule_table.yview)
        self.rule_table.configure(yscrollcommand=scroll.set)
        self.rule_table.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="删除选中", command=self._delete_rules).pack(side="left")
        ttk.Label(actions, text="内置规则不能删，但可以用同名单词加一条你自己的来覆盖",
                  style="Muted.TLabel").pack(side="left", padx=10)

        self._refresh_glossary()
        return tab

    def _glossary(self) -> Glossary:
        return Glossary.load(self.cfg.resolved_glossary_path())

    def _refresh_glossary(self) -> None:
        path = self.cfg.resolved_glossary_path()
        try:
            mine = self._glossary()
        except GlossaryError as exc:
            self.glossary_state_var.set(f"{path}\n无法读取：{exc}")
            return

        builtin: list[Rule] = []
        try:
            builtin_path = trade_glossary_path()
            if builtin_path.is_file():
                builtin = Glossary.load(builtin_path).rules
        except Exception:  # noqa: BLE001
            builtin = []

        stats = mine.stats()
        needle = self.glossary_filter_var.get().strip()
        lowered = needle.lower()

        def matches(rule: Rule) -> bool:
            if not lowered:
                return True
            haystack = " ".join((rule.src, rule.dst, rule.when, rule.note,
                                 "、".join(rule.pairs))).lower()
            return lowered in haystack

        # Saying how many terms ship with the app, not just how many are the user's own:
        # otherwise a fresh install reads "0 rules" while the app is translating with 204.
        lines = [
            path,
            f"内置外贸词库 {len(builtin)} 条，你自己加了 {stats['total']} 条"
            f"（启用 {stats['enabled']} 条）",
        ]
        for problem in mine.problems[:3]:
            lines.append(f"问题：{problem}")

        table = getattr(self, "rule_table", None)
        if table is None:
            self.glossary_state_var.set("\n".join(lines))
            return

        table.delete(*table.get_children())
        shown = 0
        # The user's own rules first: they are what the user came here to manage.
        for rules, origin, prefix in ((mine.rules, "我的", "mine"),
                                      (builtin, "内置", "builtin")):
            for index, rule in enumerate(rules):
                if not matches(rule):
                    continue
                # The index is the position in the whole list, not in what is shown, so
                # deleting still finds the rule the click pointed at.
                table.insert("", "end", iid=f"{prefix}:{index}",
                             values=(rule.src, rule.dst,
                                     "、".join(rule.pairs) or "全部",
                                     rule.when or "—",
                                     rule.note or "",
                                     origin))
                shown += 1
        if lowered:
            lines.append(f"筛选「{needle}」后显示 {shown} 条")
        self.glossary_state_var.set("\n".join(lines))

    def _delete_rules(self) -> None:
        table = getattr(self, "rule_table", None)
        if table is None:
            return
        selected = list(table.selection())
        mine = [item for item in selected if item.startswith("mine:")]
        if not mine:
            if selected:
                messagebox.showinfo(
                    "内置规则",
                    "内置的外贸词库在 app\\data\\trade_glossary.json，不能在这里删。\n\n"
                    "想改它的行为，加一条同名的「原文」规则覆盖它就行。",
                    parent=self)
            else:
                self.app._status("先选中要删除的规则")
            return
        if not messagebox.askyesno("确认删除", f"删除选中的 {len(mine)} 条规则？",
                                   parent=self):
            return
        indexes = sorted((int(item.split(":", 1)[1]) for item in mine), reverse=True)
        try:
            glossary = self._glossary()
        except GlossaryError as exc:
            messagebox.showwarning("词库有问题", str(exc), parent=self)
            return
        for index in indexes:
            if 0 <= index < len(glossary.rules):
                glossary.remove(index)
        glossary.save()
        self._refresh_glossary()
        self.app._push()
        self.app.log(f"已删除 {len(indexes)} 条词库规则")

    def _on_glossary_toggle(self) -> None:
        self.cfg.glossary_enabled = bool(self.glossary_var.get())
        self.app._push()

    def _on_redact_toggle(self) -> None:
        self.cfg.redact_enabled = bool(self.redact_var.get())
        self.app._push()

    def _add_rule(self) -> None:
        src = self.rule_src_var.get().strip()
        dst = self.rule_dst_var.get().strip()
        if not src or not dst:
            messagebox.showinfo("信息不全", "「原文」和「译文」都要填。", parent=self)
            return
        try:
            glossary = self._glossary()
        except GlossaryError as exc:
            messagebox.showwarning("词库有问题", str(exc), parent=self)
            return
        glossary.add(Rule(src=src, dst=dst, case_sensitive=False))
        glossary.save()
        self.rule_src_var.set("")
        self.rule_dst_var.set("")
        self._refresh_glossary()
        self.app._push()
        self.app.log(f"已添加词库规则：{src} → {dst}")

    def _open_glossary(self) -> None:
        path = Path(self.cfg.resolved_glossary_path())
        try:
            if not path.exists():
                Glossary([], path).save()
            os.startfile(path)  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("打不开词库文件", str(exc), parent=self)

    def _import_glossary(self) -> None:
        # A customer's term list is a spreadsheet. Making them save it as tab-separated
        # text first is the kind of thing that turns a delivered product into a support
        # call, so all three formats are offered here.
        path = filedialog.askopenfilename(
            parent=self,
            title="选择术语表",
            filetypes=[("术语表（Excel / CSV / TSV）", "*.xlsx *.xlsm *.csv *.tsv *.txt"),
                       ("Excel 工作簿", "*.xlsx *.xlsm"),
                       ("CSV", "*.csv"),
                       ("制表符文本", "*.tsv *.txt"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            glossary = self._glossary()
            added = glossary.import_table(path)
            glossary.save()
            self._refresh_glossary()
            self.app._push()
            self.app.log(f"从 {Path(path).name} 导入 {added} 条规则")
            problems = getattr(glossary, "problems", None)
            if problems:
                # A row that could not be read is worth saying out loud: a half-imported
                # glossary looks like it worked.
                messagebox.showwarning(
                    "导入完成，但有地方要留意",
                    f"导入了 {added} 条。\n\n" + "\n".join(problems[:8]), parent=self)
            else:
                self.app._status(f"已导入 {added} 条规则")
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("导入失败", str(exc), parent=self)

    def _export_glossary(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".tsv", initialfile="glossary.tsv",
            filetypes=[("制表符文本", "*.tsv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            count = self._glossary().export_tsv(path)
            self.app.log(f"已导出 {count} 条规则")
        except Exception as exc:  # noqa: BLE001
            messagebox.showwarning("导出失败", str(exc), parent=self)

    # -------------------------------------------------------------- overlay
    def _tab_overlay(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        cfg = self.cfg.overlay

        ttk.Label(tab, text="字幕外观", font=TITLE_FONT).pack(anchor="w", pady=(0, 12))

        ttk.Label(tab, text="位置", style="Muted.TLabel").pack(anchor="w")
        self.position_var = tk.StringVar(
            value={"cover": "覆盖原文", "below": "原文下方",
                   "above": "原文上方"}.get(cfg.position, "覆盖原文"))
        ttk.Combobox(tab, textvariable=self.position_var, state="readonly", width=16,
                     values=["覆盖原文", "原文下方", "原文上方"]).pack(anchor="w", pady=(4, 12))

        ttk.Label(tab, text="背景", style="Muted.TLabel").pack(anchor="w")
        self.bgmode_var = tk.StringVar(
            value={"solid": "半透明底", "transparent": "纯透明"}.get(
                cfg.background_mode, "半透明底"))
        ttk.Combobox(tab, textvariable=self.bgmode_var, state="readonly", width=16,
                     values=["半透明底", "纯透明"]).pack(anchor="w", pady=(4, 12))

        self.font_var = tk.IntVar(value=cfg.font_size)
        self.opacity_var = tk.DoubleVar(value=cfg.bg_opacity)
        for label, var, lo, hi in (("字号", self.font_var, 12, 44),
                                   ("不透明度", self.opacity_var, 0.2, 1.0)):
            ttk.Label(tab, text=f"{label}（{var.get()}）", style="Muted.TLabel").pack(anchor="w")
            ttk.Scale(tab, from_=lo, to=hi, orient="horizontal", variable=var,
                      command=lambda _v, l=label, vr=var: self._on_overlay_slider(l, vr)
                      ).pack(fill="x", pady=(4, 12))

        self.show_source_var = tk.BooleanVar(value=cfg.show_source)
        self.click_through_var = tk.BooleanVar(value=cfg.click_through)
        self.app.capture_exclude_var.set(True)
        ttk.Checkbutton(tab, text="同时显示原文", variable=self.show_source_var,
                        command=self._on_overlay_change).pack(anchor="w", pady=2)
        ttk.Checkbutton(tab, text="鼠标穿透（不挡下面窗口的点击）",
                        variable=self.click_through_var,
                        command=self._on_overlay_change).pack(anchor="w", pady=2)
        ttk.Checkbutton(tab, text="让悬浮窗对截屏隐身（覆盖模式建议开，否则会自我翻译）",
                        variable=self.app.capture_exclude_var,
                        command=self._on_overlay_change).pack(anchor="w", pady=2)
        for widget in (self.position_var, self.bgmode_var):
            widget.trace_add("write", lambda *_: self._on_overlay_change())
        return tab

    def _on_overlay_slider(self, label: str, var) -> None:
        # Redraw the label text shown next to the slider, then apply.
        self._on_overlay_change()

    def _on_overlay_change(self) -> None:
        cfg = self.cfg.overlay
        cfg.position = {"覆盖原文": "cover", "原文下方": "below",
                        "原文上方": "above"}.get(self.position_var.get(), cfg.position)
        cfg.background_mode = {"半透明底": "solid", "纯透明": "transparent"}.get(
            self.bgmode_var.get(), cfg.background_mode)
        cfg.font_size = int(float(self.font_var.get()))
        cfg.bg_opacity = round(float(self.opacity_var.get()), 2)
        cfg.show_source = bool(self.show_source_var.get())
        cfg.click_through = bool(self.click_through_var.get())
        self.app._apply_overlay_settings()
        self.app._push()

    # ------------------------------------------------------------- advanced
    def _tab_advanced(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        ttk.Label(tab, text="识别与高级", font=TITLE_FONT).pack(anchor="w")
        ttk.Label(tab, text="一般不用改。识别不准或速度不对时再调。",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 12))

        self.score_value = tk.StringVar(value=f"{self.cfg.ocr.min_score:.2f}")
        self.side_value = tk.StringVar(value=str(self.cfg.ocr.max_side_len))
        self.min_score_var = tk.DoubleVar(value=self.cfg.ocr.min_score)
        self.max_side_var = tk.IntVar(value=self.cfg.ocr.max_side_len)
        self.settle_var = tk.IntVar(value=self.cfg.settle_frames)
        self.interval_var = tk.IntVar(value=self.cfg.interval_ms)
        self.defer_var = tk.BooleanVar(value=self.cfg.defer_while_growing)

        for label, hint, var, lo, hi, value_var in (
            ("识别置信度下限", "识别不准就调低", self.min_score_var, 0.1, 0.95,
             self.score_value),
            ("识别分辨率上限", "越大越清楚，也越慢", self.max_side_var, 480, 1920,
             self.side_value),
        ):
            ttk.Label(tab, text=label, style="Muted.TLabel").pack(anchor="w")
            line = ttk.Frame(tab)
            line.pack(fill="x", pady=(4, 2))
            ttk.Scale(line, from_=lo, to=hi, orient="horizontal", variable=var,
                      command=lambda _v: self._on_advanced()).pack(
                side="left", fill="x", expand=True)
            ttk.Label(line, textvariable=value_var, width=8,
                      style="Muted.TLabel").pack(side="left", padx=(10, 0))
            ttk.Label(tab, text=hint, style="Muted.TLabel").pack(anchor="w", pady=(0, 10))

        ttk.Label(tab, text="刷新间隔（毫秒）", style="Muted.TLabel").pack(anchor="w")
        ttk.Spinbox(tab, from_=150, to=3000, increment=50, width=8,
                    textvariable=self.interval_var,
                    command=self._on_advanced).pack(anchor="w", pady=(4, 2))
        ttk.Label(tab, text="越小越灵敏，越大越省 CPU", style="Muted.TLabel").pack(
            anchor="w", pady=(0, 10))

        ttk.Label(tab, text="稳定帧数", style="Muted.TLabel").pack(anchor="w")
        ttk.Spinbox(tab, from_=1, to=5, width=8, textvariable=self.settle_var,
                    command=self._on_advanced).pack(anchor="w", pady=(4, 2))
        ttk.Label(tab, text="连续几帧一致才翻译，越大越稳越慢", style="Muted.TLabel").pack(
            anchor="w", pady=(0, 10))

        ttk.Checkbutton(tab, text="字幕逐字浮现时，等成型再翻译",
                        variable=self.defer_var,
                        command=self._on_advanced).pack(anchor="w", pady=(4, 0))
        return tab

    def _on_advanced(self) -> None:
        self.cfg.ocr.min_score = float(self.min_score_var.get())
        self.cfg.ocr.max_side_len = int(self.max_side_var.get())
        self.cfg.settle_frames = int(self.settle_var.get())
        self.cfg.interval_ms = int(self.interval_var.get())
        self.cfg.defer_while_growing = bool(self.defer_var.get())
        self.score_value.set(f"{self.cfg.ocr.min_score:.2f}")
        self.side_value.set(str(self.cfg.ocr.max_side_len))
        self.app._push()

    # ------------------------------------------------------------------- log
    def _tab_log(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        ttk.Label(tab, text="运行日志", font=TITLE_FONT).pack(anchor="w")
        ttk.Label(tab, text="出问题时把这里的内容截下来。",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 8))
        self.log_text = tk.Text(tab, wrap="word", bg=CARD, fg=MUTED, relief="flat",
                                padx=10, pady=8, borderwidth=0,
                                font=("Consolas", 9), height=16)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        ttk.Button(tab, text="复制全部", command=self._copy_log).pack(anchor="e", pady=(8, 0))
        return tab

    def append_log(self, message: str) -> None:
        self._log_buffer.append(message)
        if len(self._log_buffer) > 500:
            self._log_buffer = self._log_buffer[-400:]
        widget = getattr(self, "log_text", None)
        if widget is None:
            return
        try:
            widget.configure(state="normal")
            widget.insert("end", message + "\n")
            widget.see("end")
            widget.configure(state="disabled")
        except Exception:  # noqa: BLE001
            pass

    def _copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append("\n".join(self._log_buffer))
        self.app._status("日志已复制")

    # -------------------------------------------------------------- about
    def _tab_about(self, parent: tk.Misc) -> ttk.Frame:
        tab = ttk.Frame(parent, padding=16)
        ttk.Label(tab, text="关于", font=TITLE_FONT).pack(anchor="w")
        ttk.Label(tab, text=f"守译 RuZh {APP_VERSION}　·　离线优先的中俄翻译工具",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 10))

        # The dictionary is CC BY-SA 3.0 and the Russian route runs through a CC-BY-4.0
        # model, so both require attribution. A notice buried in app/data/README.md is not
        # attribution anybody will ever see, which is why this is a tab rather than a file.
        box = tk.Text(tab, wrap="word", bg=CARD, fg=MUTED, relief="flat",
                      padx=12, pady=10, borderwidth=0,
                      font=("Microsoft YaHei UI", 9))
        box.pack(fill="both", expand=True)
        box.insert("1.0", ABOUT_TEXT)
        box.configure(state="disabled")

        row = ttk.Frame(tab)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="复制全部",
                   command=lambda: self._copy_about(box)).pack(side="left")
        ttk.Button(row, text="打开软件目录",
                   command=self._open_app_dir).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="打开署名文件",
                   command=self._open_notices).pack(side="left", padx=(8, 0))
        return tab

    def _copy_about(self, box: tk.Text) -> None:
        self.clipboard_clear()
        self.clipboard_append(box.get("1.0", "end").strip())
        self.app._status("关于信息已复制")

    def _open_app_dir(self) -> None:
        try:
            os.startfile(str(config_mod.APP_DIR))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            self.app._status(f"打不开：{exc}")

    def _open_notices(self) -> None:
        path = config_mod.APP_DIR / "app" / "data" / "README.md"
        if not path.is_file():
            self.app._status(f"找不到署名文件：{path}")
            return
        try:
            os.startfile(str(path))  # noqa: S606
        except Exception as exc:  # noqa: BLE001
            self.app._status(f"打不开：{exc}")


class WindowPicker(tk.Toplevel):
    """Pick the window to follow, from the ones currently on screen."""

    def __init__(self, app: App, windows: list, on_choose) -> None:
        super().__init__(app)
        self.windows = windows
        self.on_choose = on_choose
        self.title("选择要翻译的窗口")
        self.configure(bg=BG)
        self.geometry("560x420")
        self.transient(app)

        ttk.Label(self, text="选择要翻译的窗口", style="Title.TLabel").pack(
            anchor="w", padx=16, pady=(16, 4))
        ttk.Label(self, text="选好后，窗口拖到哪，译文就跟到哪。",
                  style="Muted.TLabel").pack(anchor="w", padx=16, pady=(0, 10))

        frame = ttk.Frame(self, padding=(16, 0))
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(frame, bg=CARD, fg=TEXT, relief="flat",
                                  selectbackground=ACCENT, selectforeground="#ffffff",
                                  borderwidth=0, highlightthickness=1,
                                  highlightbackground=BORDER,
                                  activestyle="none", font=BODY_FONT)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scroll.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for info in windows:
            self.listbox.insert("end", f"  {info.label}   "
                                       f"[{info.client_width}×{info.client_height}]")
        if windows:
            self.listbox.selection_set(0)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._choose())

        buttons = ttk.Frame(self, padding=16)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="就是它", style="Primary.TButton",
                   command=self._choose).pack(side="right", padx=8)

    def _choose(self) -> None:
        selection = self.listbox.curselection()
        if not selection:
            return
        info = self.windows[selection[0]]
        self.destroy()
        self.on_choose(info)


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()
    app = App(cfg)
    app.mainloop()
    return 0
