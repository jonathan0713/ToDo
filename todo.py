from __future__ import annotations

import ctypes
import copy
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import uuid
from datetime import datetime
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Any

from launcher import LaunchError, RunningApplication, launch, list_running_applications
from task_store import (
    AUTO_GROUP_ID,
    DAILY,
    PERMANENT,
    TaskStore,
    UNGROUPED_ID,
    is_task_complete,
    normalize_launch_item,
    set_task_complete,
)


APP_NAME = "TodoLauncher"
MUTEX_NAME = rf"Local\{APP_NAME}-SingleInstance"
UI_FONT = "Microsoft JhengHei UI"
UI_FONT_DISPLAY = "Microsoft JhengHei UI"
UI_FONT_ICON = "Segoe UI Symbol"

# Neutral-first dark theme inspired by Fluent's semantic color roles. Accent and
# status colors are deliberately reserved for actions, selection, and feedback.
COLOR_CHROME = "#0e1014"
COLOR_CANVAS = "#13161b"
COLOR_SURFACE = "#191d24"
COLOR_SURFACE_RAISED = "#20252e"
COLOR_SURFACE_HOVER = "#282e39"
COLOR_BORDER = "#2c323d"
COLOR_BORDER_STRONG = "#3a4250"
COLOR_TEXT = "#f2f4f7"
COLOR_TEXT_SECONDARY = "#aab2bf"
COLOR_TEXT_MUTED = "#7e8897"
COLOR_ACCENT = "#8191f7"
COLOR_ACCENT_HOVER = "#91a0ff"
COLOR_ACCENT_SUBTLE = "#29304a"
COLOR_SUCCESS = "#66c990"
COLOR_WARNING = "#e3b762"
COLOR_DANGER = "#ed777f"
GROUP_COLORS = {
    "天空藍": "#78a9ff",
    "薄荷綠": "#70c58b",
    "暖橙色": "#d99559",
    "柔紫色": "#b39ddb",
    "玫瑰紅": "#ef7f91",
    "中性灰": "#9aa3af",
}
PANEL_WIDTHS = {"窄版 · 420": 420, "標準 · 460": 460, "寬版 · 540": 540, "加寬 · 620": 620}
GROUP_RULE_LABELS = {
    "手動內容": "manual",
    "自動 · 未完成": "unfinished",
    "自動 · 已完成": "completed",
    "自動 · 每日任務": "daily",
    "自動 · 永久任務": "permanent",
}


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    bundle_dir = Path(getattr(sys, "_MEIPASS", application_dir()))
    return bundle_dir / name


def user_data_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_NAME / "tasks.json"


class SingleInstance:
    def __init__(self) -> None:
        self.handle: int | None = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        self.handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return bool(self.handle) and kernel32.GetLastError() != 183

    def release(self) -> None:
        if self.handle and os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(self.handle)
            self.handle = None


class TodoApp:
    def __init__(self, root: tk.Tk, store: TaskStore) -> None:
        self.root = root
        self.store = store
        self.data = store.load()
        self.visible = False
        self.launching = False
        self.launching_task_index: int | None = None
        self.failed_task_indices: set[int] = set()
        self.visible_task_indices: list[int] = []
        self.task_cards: dict[int, tk.Frame] = {}
        self.group_headers: dict[str, tk.Frame] = {}
        self.drag_task_index: int | None = None
        self.drag_start: tuple[int, int] | None = None
        self.drag_moved = False
        self.drop_target: tuple[str, Any, str | None] | None = None
        self.drop_highlight: tk.Widget | None = None
        self.toast_window: tk.Toplevel | None = None
        self.tray_icon: Any | None = None
        self.tray_thread: threading.Thread | None = None
        self.actions: queue.Queue[str] = queue.Queue()

        self._configure_window()
        self._build_ui()
        self._build_arrow_window()
        self.update_listbox()
        self.hide_window()
        self._start_tray()
        self.root.after(100, self._poll_actions)

        if self.store.last_warning:
            self.root.after(
                150,
                lambda: messagebox.showwarning("資料讀取警告", self.store.last_warning),
            )

    def _configure_window(self) -> None:
        self.root.title("TodoLauncher")
        self.root.overrideredirect(True)
        self.root.configure(bg=COLOR_CHROME)
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        left, top, right, bottom = self._work_area()
        self.work_left = left
        self.work_top = top
        self.work_right = right
        self.work_bottom = bottom
        work_width, work_height = right - left, bottom - top
        requested_width = self.data["settings"].get("panel_width", 460)
        self.window_width = min(max(420, requested_width), min(620, work_width))
        self.window_height = min(max(520, int(work_height * 0.65)), 760)
        self.panel_x = right - self.window_width
        self.panel_y = bottom - self.window_height
        self.edge_x = right
        self.root.geometry(
            f"{self.window_width}x{self.window_height}+{self.panel_x}+{self.panel_y}"
        )

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Secondary.TButton",
            background=COLOR_SURFACE_RAISED,
            foreground=COLOR_TEXT,
            bordercolor=COLOR_BORDER,
            borderwidth=0,
            relief="flat",
            padding=(14, 8),
            font=(UI_FONT, 10),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", COLOR_SURFACE_HOVER)],
            foreground=[("disabled", COLOR_TEXT_MUTED)],
        )
        style.configure(
            "TCheckbutton",
            background=COLOR_CANVAS,
            foreground=COLOR_TEXT,
            font=(UI_FONT, 10),
        )
        style.configure(
            "Group.TCheckbutton",
            background=COLOR_SURFACE_RAISED,
            foreground=COLOR_TEXT,
            font=(UI_FONT, 10),
        )
        style.configure(
            "Daily.Horizontal.TProgressbar",
            troughcolor=COLOR_BORDER,
            background=COLOR_ACCENT,
            bordercolor=COLOR_BORDER,
            lightcolor=COLOR_ACCENT,
            darkcolor=COLOR_ACCENT,
            thickness=4,
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            troughcolor=COLOR_CANVAS,
            background=COLOR_BORDER_STRONG,
            bordercolor=COLOR_CANVAS,
            arrowcolor=COLOR_TEXT_SECONDARY,
            lightcolor=COLOR_BORDER_STRONG,
            darkcolor=COLOR_BORDER_STRONG,
            width=8,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground=COLOR_SURFACE,
            background=COLOR_SURFACE_RAISED,
            foreground=COLOR_TEXT,
            arrowcolor=COLOR_TEXT_SECONDARY,
            bordercolor=COLOR_BORDER_STRONG,
            padding=6,
            font=(UI_FONT, 10),
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", COLOR_SURFACE)],
            foreground=[("readonly", COLOR_TEXT)],
            selectbackground=[("readonly", COLOR_SURFACE)],
            selectforeground=[("readonly", COLOR_TEXT)],
        )

    @staticmethod
    def _work_area() -> tuple[int, int, int, int]:
        if os.name == "nt":
            class Rect(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            rect = Rect()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
                return rect.left, rect.top, rect.right, rect.bottom
        return 0, 0, 1280, 720

    def _dialog_geometry(self, width: int, height: int) -> str:
        preferred_x = self.root.winfo_x() + (self.window_width - width) // 2
        preferred_y = self.root.winfo_y() + (self.window_height - height) // 2
        x = max(self.work_left, min(preferred_x, self.work_right - width))
        y = max(self.work_top, min(preferred_y, self.work_bottom - height))
        return f"{width}x{height}+{x}+{y}"

    def _apply_panel_width(self) -> None:
        work_width = self.work_right - self.work_left
        requested = self.data["settings"].get("panel_width", 460)
        self.window_width = min(max(420, requested), min(620, work_width))
        self.panel_x = self.work_right - self.window_width
        if self.visible:
            self.root.geometry(
                f"{self.window_width}x{self.window_height}+{self.panel_x}+{self.panel_y}"
            )
            self.arrow_window.geometry(
                f"{self.arrow_width}x{self.arrow_height}+{self.panel_x - self.arrow_width}+{self.panel_y}"
            )

    def _build_ui(self) -> None:
        title_bar = tk.Frame(self.root, bg=COLOR_CHROME, height=44)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)
        title_bar.bind("<Button-1>", self._start_move)
        title_bar.bind("<B1-Motion>", self._move_app)

        title_label = tk.Label(
            title_bar,
            text="TodoLauncher",
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_CHROME,
            anchor="w",
            font=(UI_FONT, 9, "bold"),
        )
        title_label.pack(side=tk.LEFT, padx=18, fill=tk.Y)
        title_label.bind("<Button-1>", self._start_move)
        title_label.bind("<B1-Motion>", self._move_app)

        tk.Button(
            title_bar,
            text="×",
            command=self.hide_window,
            fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_CHROME,
            activebackground=COLOR_SURFACE_RAISED,
            activeforeground=COLOR_TEXT,
            bd=0,
            width=5,
            font=(UI_FONT_ICON, 13),
            cursor="hand2",
        ).pack(side=tk.RIGHT, fill=tk.Y)

        body = tk.Frame(self.root, bg=COLOR_CANVAS)
        body.pack(fill=tk.BOTH, expand=True)

        summary = tk.Frame(body, bg=COLOR_CANVAS)
        summary.pack(fill=tk.X, padx=20, pady=(16, 12))
        summary_row = tk.Frame(summary, bg=COLOR_CANVAS)
        summary_row.pack(fill=tk.X, pady=(0, 9))
        self.date_label = tk.Label(
            summary_row,
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9),
        )
        self.date_label.pack(side=tk.LEFT)
        self.progress_label = tk.Label(
            summary_row,
            fg=COLOR_ACCENT,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9, "bold"),
        )
        self.progress_label.pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(
            summary,
            maximum=100,
            mode="determinate",
            style="Daily.Horizontal.TProgressbar",
        )
        self.progress.pack(fill=tk.X)

        list_shell = tk.Frame(body, bg=COLOR_CANVAS)
        list_shell.pack(fill=tk.BOTH, expand=True, padx=(14, 8))
        self.cards_canvas = tk.Canvas(
            list_shell,
            bg=COLOR_CANVAS,
            highlightthickness=0,
            bd=0,
        )
        self.cards_scrollbar = ttk.Scrollbar(
            list_shell,
            orient=tk.VERTICAL,
            command=self.cards_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.cards_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.cards_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.cards_canvas.configure(yscrollcommand=self._update_scrollbar)
        self.cards_frame = tk.Frame(self.cards_canvas, bg=COLOR_CANVAS)
        self.cards_window = self.cards_canvas.create_window(
            (0, 0), window=self.cards_frame, anchor="nw"
        )
        self.cards_frame.bind(
            "<Configure>",
            lambda _event: self.cards_canvas.configure(
                scrollregion=self.cards_canvas.bbox("all")
            ),
        )
        self.cards_canvas.bind(
            "<Configure>",
            lambda event: self.cards_canvas.itemconfigure(
                self.cards_window, width=event.width
            ),
        )
        # Bind at the main toplevel so wheel input works over cards, labels,
        # buttons, the header, and empty space—not only over the scrollbar.
        self.root.bind("<MouseWheel>", self._on_mousewheel, add="+")

        footer = tk.Frame(body, bg=COLOR_CANVAS)
        footer.pack(fill=tk.X, padx=20, pady=(12, 16))
        tk.Button(
            footer,
            text="＋  新增任務",
            command=self.add_task,
            bg=COLOR_ACCENT,
            fg=COLOR_CHROME,
            activebackground=COLOR_ACCENT_HOVER,
            activeforeground=COLOR_CHROME,
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=8,
            font=(UI_FONT, 10, "bold"),
            cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            footer,
            text="從執行中新增",
            command=self.add_running_app_task,
            bg=COLOR_SURFACE_RAISED,
            fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_SURFACE_HOVER,
            activeforeground=COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=14,
            pady=8,
            font=(UI_FONT, 9),
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            footer,
            text="設定",
            command=self.open_settings,
            bg=COLOR_CANVAS,
            fg=COLOR_TEXT_MUTED,
            activebackground=COLOR_SURFACE_HOVER,
            activeforeground=COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=8,
            font=(UI_FONT, 9),
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        self.root.bind("<Return>", lambda _event: self.launch_selected())
        self.root.bind("<space>", lambda _event: self.toggle_done())
        self.root.bind("<Delete>", lambda _event: self.delete_task())
        self.root.bind("<Insert>", lambda _event: self.add_task())
        self.root.bind("<Escape>", lambda _event: self.hide_window())
        self.root.bind("<Up>", lambda _event: self._move_selection(-1))
        self.root.bind("<Down>", lambda _event: self._move_selection(1))

    def _build_arrow_window(self) -> None:
        self.arrow_width = 28
        self.arrow_height = 64
        self.arrow_window = tk.Toplevel(self.root)
        self.arrow_window.overrideredirect(True)
        self.arrow_window.attributes("-topmost", True)
        self.arrow_window.attributes("-alpha", 0.92)
        self.arrow_button = tk.Button(
            self.arrow_window,
            command=self.toggle_window,
            text="‹",
            bg=COLOR_CANVAS,
            fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_SURFACE_HOVER,
            activeforeground=COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            font=(UI_FONT_ICON, 24),
            cursor="hand2",
        )
        self.arrow_button.pack(fill=tk.BOTH, expand=True)
        self.arrow_menu = tk.Menu(
            self.arrow_window,
            tearoff=False,
            bg=COLOR_SURFACE_RAISED,
            fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_ACCENT_SUBTLE,
            activeforeground=COLOR_TEXT,
            bd=0,
            font=(UI_FONT, 9),
        )
        self.arrow_menu.add_command(label="顯示視窗", command=self.show_window)
        self.arrow_menu.add_separator()
        self.arrow_menu.add_command(label="退出", command=self.exit_app)
        self.arrow_button.bind("<Button-3>", self._show_arrow_menu)

    def _set_arrow(self, expanded: bool) -> None:
        self.arrow_button.configure(text="›" if expanded else "‹")

    def _show_arrow_menu(self, event: tk.Event) -> None:
        self.arrow_menu.tk_popup(event.x_root, event.y_root)

    def _selected_index(self) -> int | None:
        selected = getattr(self, "selected_task_index", None)
        if selected is None or selected >= len(self.data["tasks"]):
            return None
        return selected

    @staticmethod
    def _status_text(task: dict[str, Any]) -> str:
        value = task.get("last_completed_at")
        if not value:
            return "尚未完成"
        try:
            completed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if completed.tzinfo is None:
                completed = completed.astimezone()
            completed = completed.astimezone()
        except ValueError:
            return "尚未完成"
        if is_task_complete(task):
            if task["completion_mode"] == DAILY:
                return f"今日 {completed:%H:%M} 完成"
            return f"{completed:%m/%d %H:%M} 完成"
        return f"上次 {completed:%m/%d %H:%M}"

    @staticmethod
    def _matches_group_rule(task: dict[str, Any], rule: str) -> bool:
        if rule == "unfinished":
            return not is_task_complete(task)
        if rule == "completed":
            return is_task_complete(task)
        if rule == "daily":
            return task.get("completion_mode", DAILY) == DAILY
        if rule == "permanent":
            return task.get("completion_mode") == PERMANENT
        return False

    def _effective_group_id(self, task: dict[str, Any]) -> str:
        group_id = task.get("group_id", UNGROUPED_ID)
        if group_id != AUTO_GROUP_ID:
            return group_id
        for group in self.data["groups"]:
            if self._matches_group_rule(task, group.get("rule", "manual")):
                return group["id"]
        return UNGROUPED_ID

    def update_listbox(self, selected_index: int | None = None) -> None:
        if selected_index is not None:
            self.selected_task_index = selected_index
        elif not hasattr(self, "selected_task_index"):
            self.selected_task_index = None
        for child in self.cards_frame.winfo_children():
            child.destroy()
        self.task_cards = {}
        self.group_headers = {}

        tasks = self.data["tasks"]
        completed_count = sum(is_task_complete(task) for task in tasks)
        total_count = len(tasks)
        weekdays = "一二三四五六日"
        today = datetime.now()
        self.date_label.configure(
            text=f"{today.month}月{today.day}日  星期{weekdays[today.weekday()]}"
        )
        self.progress_label.configure(text=f"{completed_count} / {total_count} 完成")
        self.progress.configure(
            value=(completed_count / total_count * 100) if total_count else 0
        )

        self.visible_task_indices = []
        display_groups = [*self.data["groups"], {
            "id": UNGROUPED_ID,
            "name": "未分類",
            "icon": "○",
            "color": "#7f8a99",
            "batch_launch": False,
            "collapsed": self.data["settings"].get("ungrouped_collapsed", False),
        }]
        for group in display_groups:
            group_indices = [
                task_index
                for task_index, task in enumerate(tasks)
                if self._effective_group_id(task) == group["id"]
            ]
            if not group_indices:
                continue
            group_indices.sort(
                key=lambda task_index: (is_task_complete(tasks[task_index]), task_index)
            )
            group_complete = sum(is_task_complete(tasks[index]) for index in group_indices)
            section = tk.Frame(
                self.cards_frame,
                bg=COLOR_CANVAS,
                highlightthickness=1,
                highlightbackground=COLOR_CANVAS,
            )
            section._group_id = group["id"]
            self.group_headers[group["id"]] = section
            section.pack(fill=tk.X, padx=(8, 10), pady=(12, 3))
            tk.Frame(section, bg=group["color"], width=3, height=20).pack(
                side=tk.LEFT, padx=(0, 7)
            )
            collapsed = bool(group.get("collapsed", False))
            toggle = tk.Button(
                section,
                text="›" if collapsed else "⌄",
                command=lambda group_id=group["id"]: self._toggle_group_collapsed(group_id),
                fg=group["color"],
                bg=COLOR_CANVAS,
                activebackground=COLOR_CANVAS,
                activeforeground=group["color"],
                relief=tk.FLAT,
                bd=0,
                width=2,
                font=(UI_FONT_ICON, 11),
                cursor="hand2",
            )
            toggle.pack(side=tk.LEFT)
            group_label = tk.Label(
                section,
                text=f"{group['icon']}  {group['name']}",
                fg=COLOR_TEXT_SECONDARY,
                bg=COLOR_CANVAS,
                font=(UI_FONT, 10, "bold"),
                cursor="hand2",
            )
            group_label.pack(side=tk.LEFT)
            group_label.bind(
                "<Button-1>",
                lambda _event, group_id=group["id"]: self._toggle_group_collapsed(group_id),
            )
            if group.get("batch_launch"):
                tk.Button(
                    section,
                    text="啟動未完成",
                    command=lambda group_id=group["id"]: self.launch_group(group_id),
                    state=tk.DISABLED if self.launching else tk.NORMAL,
                    bg=COLOR_SURFACE,
                    fg=group["color"],
                    activebackground=COLOR_SURFACE_HOVER,
                    activeforeground=group["color"],
                    relief=tk.FLAT,
                    bd=0,
                    padx=9,
                    pady=3,
                    font=(UI_FONT, 8, "bold"),
                    cursor="hand2",
                ).pack(side=tk.RIGHT)
            tk.Label(
                section,
                text=f"{group_complete}/{len(group_indices)}",
                fg=COLOR_TEXT_MUTED,
                bg=COLOR_CANVAS,
                font=(UI_FONT, 9),
            ).pack(side=tk.RIGHT, padx=(0, 9))
            if collapsed:
                continue
            self.visible_task_indices.extend(group_indices)
            for task_index in group_indices:
                self._render_task_card(task_index)

        if not tasks:
            empty = tk.Frame(self.cards_frame, bg=COLOR_SURFACE)
            empty.pack(fill=tk.BOTH, expand=True, padx=(6, 8), pady=6)
            tk.Label(
                empty,
                text="今天還沒有任務",
                fg=COLOR_TEXT,
                bg=COLOR_SURFACE,
                font=(UI_FONT_DISPLAY, 15, "bold"),
            ).pack(pady=(60, 6))
            tk.Label(
                empty,
                text="建立第一個每日啟動項目，之後只要按一次「啟動」。",
                fg=COLOR_TEXT_MUTED,
                bg=COLOR_SURFACE,
                font=(UI_FONT, 10),
                wraplength=320,
            ).pack(pady=(0, 60))

    def _render_task_card(self, task_index: int) -> None:
        task = self.data["tasks"][task_index]
        compact = self.data["settings"].get("density") == "compact"
        show_details = self.data["settings"].get("show_task_details", True)
        selected = task_index == self.selected_task_index
        complete = is_task_complete(task)
        if task_index == self.launching_task_index:
            marker, accent, status = "●", COLOR_WARNING, "正在啟動…"
        elif task_index in self.failed_task_indices:
            marker, accent, status = "●", COLOR_DANGER, "啟動失敗"
        elif complete:
            marker, accent, status = "●", COLOR_SUCCESS, self._status_text(task)
        else:
            marker, accent, status = "○", COLOR_TEXT_MUTED, self._status_text(task)

        card_bg = COLOR_ACCENT_SUBTLE if selected else COLOR_SURFACE
        card = tk.Frame(
            self.cards_frame,
            bg=card_bg,
            highlightthickness=1,
            highlightbackground=COLOR_ACCENT if selected else COLOR_BORDER,
            cursor="hand2",
        )
        card._task_index = task_index
        self.task_cards[task_index] = card
        card.pack(fill=tk.X, padx=(6, 8), pady=2 if compact else 5)
        card.grid_columnconfigure(2, weight=1)
        row_span = 2 if show_details else 1
        grip = tk.Label(
            card, text="⋮", fg=COLOR_TEXT_MUTED, bg=card_bg,
            font=(UI_FONT_ICON, 11), width=1, cursor="fleur",
        )
        grip.grid(
            row=0, column=0, rowspan=row_span, padx=(5, 0),
            pady=5 if compact else 10,
        )
        dot = tk.Label(card, text=marker, fg=accent, bg=card_bg,
                       font=(UI_FONT_ICON, 13 if compact else 16), width=2)
        dot.grid(
            row=0, column=1, rowspan=row_span,
            padx=((2, 2) if compact else (3, 4)), pady=5 if compact else 10,
        )
        title = tk.Label(
            card, text=task["task"],
            fg=COLOR_TEXT_SECONDARY if complete else COLOR_TEXT,
            bg=card_bg,
            font=(UI_FONT, 10 if compact else 11, "bold"),
            anchor="w",
        )
        title.grid(
            row=0, column=2, sticky="ew",
            pady=((6, 0) if compact and show_details else ((8, 8) if compact else ((10, 0) if show_details else (13, 13)))),
        )
        bound_widgets = [card, grip, dot, title]
        if show_details:
            mode = "每日" if task["completion_mode"] == DAILY else "永久"
            meta = tk.Label(
                card,
                text=f"{len(task['quick_launch'])} 個啟動項  ·  {mode}  ·  {status}",
                fg=COLOR_TEXT_MUTED, bg=card_bg,
                font=(UI_FONT, 8 if compact else 9), anchor="w",
            )
            meta.grid(
                row=1, column=2, sticky="ew",
                pady=(0, 6) if compact else (1, 10),
            )
            bound_widgets.append(meta)
        tk.Button(
            card,
            text="啟動中" if task_index == self.launching_task_index else ("再開" if complete else "啟動"),
            command=lambda: self.launch_task(task_index),
            state=tk.DISABLED if self.launching else tk.NORMAL,
            bg=COLOR_ACCENT if not complete else COLOR_SURFACE_RAISED,
            fg=COLOR_CHROME if not complete else COLOR_TEXT_SECONDARY,
            activebackground=COLOR_ACCENT_HOVER,
            activeforeground=COLOR_CHROME,
            disabledforeground=COLOR_TEXT_MUTED, relief=tk.FLAT, bd=0,
            padx=10 if compact else 14, pady=5 if compact else 7,
            font=(UI_FONT, 8 if compact else 9, "bold"), cursor="hand2",
        ).grid(
            row=0, column=3, rowspan=row_span, padx=(8, 4),
            pady=7 if compact else 14,
        )
        tk.Button(
            card, text="⋯", command=lambda: self._show_task_menu(task_index),
            bg=card_bg, fg=COLOR_TEXT_MUTED,
            activebackground=COLOR_SURFACE_HOVER,
            activeforeground=COLOR_TEXT, relief=tk.FLAT, bd=0, width=3,
            font=(UI_FONT_ICON, 11 if compact else 12), cursor="hand2",
        ).grid(
            row=0, column=4, rowspan=row_span, padx=(0, 5),
            pady=7 if compact else 14,
        )
        for widget in bound_widgets:
            widget.configure(cursor="fleur")
            widget.bind(
                "<ButtonPress-1>",
                lambda event, index=task_index: self._begin_task_drag(index, event),
            )
            widget.bind("<B1-Motion>", self._drag_task)
            widget.bind("<ButtonRelease-1>", self._finish_task_drag)
            widget.bind("<Double-Button-1>", lambda _event: self.launch_task(task_index))

    def _begin_task_drag(self, task_index: int, event: tk.Event) -> None:
        self.drag_task_index = task_index
        self.drag_start = (event.x_root, event.y_root)
        self.drag_moved = False
        self.drop_target = None
        self.selected_task_index = task_index

    def _drag_task(self, event: tk.Event) -> None:
        if self.drag_task_index is None or self.drag_start is None:
            return
        if not self.drag_moved:
            distance = abs(event.x_root - self.drag_start[0]) + abs(
                event.y_root - self.drag_start[1]
            )
            if distance < 8:
                return
            self.drag_moved = True
            self.root.configure(cursor="fleur")

        target = self._drop_target_at(event.x_root, event.y_root)
        self._show_drop_target(target)

    def _drop_target_at(
        self, x_root: int, y_root: int
    ) -> tuple[str, Any, str | None] | None:
        widget = self.root.winfo_containing(x_root, y_root)
        while widget is not None:
            task_index = getattr(widget, "_task_index", None)
            if task_index is not None and task_index != self.drag_task_index:
                card = self.task_cards.get(task_index)
                position = (
                    "before"
                    if card and y_root < card.winfo_rooty() + card.winfo_height() / 2
                    else "after"
                )
                return ("task", task_index, position)
            group_id = getattr(widget, "_group_id", None)
            if group_id is not None:
                return ("group", group_id, None)
            widget = getattr(widget, "master", None)
        return None

    def _show_drop_target(
        self, target: tuple[str, Any, str | None] | None
    ) -> None:
        if target == self.drop_target:
            return
        if self.drop_highlight is not None and self.drop_highlight.winfo_exists():
            if getattr(self.drop_highlight, "_task_index", None) is not None:
                index = self.drop_highlight._task_index
                selected = index == self.selected_task_index
                self.drop_highlight.configure(
                    highlightbackground=COLOR_ACCENT if selected else COLOR_BORDER
                )
            else:
                self.drop_highlight.configure(highlightbackground=COLOR_CANVAS)
        self.drop_target = target
        self.drop_highlight = None
        if target is None:
            return
        kind, value, _position = target
        widget = (
            self.task_cards.get(value)
            if kind == "task"
            else self.group_headers.get(value)
        )
        if widget is not None:
            widget.configure(highlightbackground=COLOR_WARNING)
            self.drop_highlight = widget

    def _finish_task_drag(self, _event: tk.Event) -> None:
        source_index = self.drag_task_index
        target = self.drop_target
        moved = self.drag_moved
        self.root.configure(cursor="")
        self.drag_task_index = None
        self.drag_start = None
        self.drag_moved = False
        self.drop_target = None
        self.drop_highlight = None
        if source_index is None:
            return
        if not moved or target is None:
            self.update_listbox(source_index)
            return
        self._move_task_to_drop_target(source_index, target)

    def _move_task_to_drop_target(
        self, source_index: int, target: tuple[str, Any, str | None]
    ) -> None:
        tasks = self.data["tasks"]
        original_tasks = tasks.copy()
        original_group_id = tasks[source_index].get("group_id", UNGROUPED_ID)
        source_task = tasks[source_index]
        kind, value, position = target
        if kind == "task":
            target_task = tasks[value]
            source_effective_group = self._effective_group_id(source_task)
            target_effective_group = self._effective_group_id(target_task)
            both_are_auto_peers = (
                source_task.get("group_id") == AUTO_GROUP_ID
                and target_task.get("group_id") == AUTO_GROUP_ID
                and source_effective_group == target_effective_group
            )
            if not both_are_auto_peers:
                source_task["group_id"] = target_effective_group
            tasks.pop(source_index)
            target_index = tasks.index(target_task)
            insert_at = target_index + (1 if position == "after" else 0)
        else:
            source_task["group_id"] = value
            tasks.pop(source_index)
            group_positions = [
                index
                for index, task in enumerate(tasks)
                if self._effective_group_id(task) == value
            ]
            insert_at = group_positions[-1] + 1 if group_positions else len(tasks)
        tasks.insert(insert_at, source_task)
        if self._save():
            self.selected_task_index = insert_at
            self.update_listbox(insert_at)
            self.show_toast("任務順序與群組已更新")
        else:
            source_task["group_id"] = original_group_id
            self.data["tasks"] = original_tasks
            self.update_listbox(source_index)

    def _toggle_group_collapsed(self, group_id: str) -> None:
        if group_id == UNGROUPED_ID:
            key = "ungrouped_collapsed"
            self.data["settings"][key] = not self.data["settings"].get(key, False)
        else:
            group = next(
                (group for group in self.data["groups"] if group["id"] == group_id),
                None,
            )
            if group is None:
                return
            group["collapsed"] = not group.get("collapsed", False)
        if self._save():
            self.update_listbox()

    def _select_task(self, index: int) -> None:
        self.selected_task_index = index
        self.update_listbox(index)

    def _move_selection(self, direction: int) -> None:
        if not self.visible_task_indices:
            return
        current = self._selected_index()
        if current not in self.visible_task_indices:
            next_position = 0
        else:
            next_position = max(
                0,
                min(
                    len(self.visible_task_indices) - 1,
                    self.visible_task_indices.index(current) + direction,
                ),
            )
        self._select_task(self.visible_task_indices[next_position])

    def _on_mousewheel(self, event: tk.Event) -> None:
        if not self.visible:
            return
        direction = -1 if event.delta > 0 else 1
        self.cards_canvas.yview_scroll(direction * 3, "units")

    def _update_scrollbar(self, first: str, last: str) -> None:
        self.cards_scrollbar.set(first, last)
        content_fits = float(first) <= 0 and float(last) >= 1
        if content_fits and self.cards_scrollbar.winfo_manager():
            self.cards_scrollbar.pack_forget()
        elif not content_fits and not self.cards_scrollbar.winfo_manager():
            self.cards_scrollbar.pack(
                side=tk.RIGHT, fill=tk.Y, before=self.cards_canvas
            )

    def _show_task_menu(self, index: int) -> None:
        self.selected_task_index = index
        task = self.data["tasks"][index]
        menu = tk.Menu(
            self.root,
            tearoff=False,
            bg=COLOR_SURFACE_RAISED,
            fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_ACCENT_SUBTLE,
            activeforeground=COLOR_TEXT,
            bd=0,
            font=(UI_FONT, 9),
        )
        menu.add_command(label="立即啟動", command=lambda: self.launch_task(index))
        menu.add_separator()
        menu.add_command(label="編輯任務與啟動項", command=lambda: self.edit_task(index))
        done_label = "標記為未完成" if is_task_complete(task) else "標記為完成"
        menu.add_command(label=done_label, command=lambda: self.toggle_done(index))
        menu.add_separator()
        menu.add_command(label="刪除任務", command=lambda: self.delete_task(index))
        menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())

    def _save(self) -> bool:
        try:
            self.store.save(self.data)
            return True
        except (OSError, ValueError) as error:
            messagebox.showerror("儲存失敗", str(error), parent=self.root)
            return False

    def add_task(self) -> None:
        self._open_task_editor()

    def add_running_app_task(self) -> None:
        self._open_running_app_picker()

    def _open_running_app_picker(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("從執行中程式新增")
        dialog.geometry(self._dialog_geometry(660, 520))
        dialog.minsize(520, 420)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg=COLOR_CANVAS)

        header = tk.Frame(dialog, bg=COLOR_CHROME, height=72)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        title_block = tk.Frame(header, bg=COLOR_CHROME)
        title_block.pack(side=tk.LEFT, padx=20, fill=tk.Y)
        tk.Label(
            title_block,
            text="選擇執行中的程式",
            fg=COLOR_TEXT,
            bg=COLOR_CHROME,
            font=(UI_FONT_DISPLAY, 17, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(10, 0))
        tk.Label(
            title_block,
            text="只顯示目前有可見視窗、且能取得執行檔路徑的程式",
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_CHROME,
            font=(UI_FONT, 9),
            anchor="w",
        ).pack(anchor="w")

        body = tk.Frame(dialog, bg=COLOR_CANVAS)
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)

        search_row = tk.Frame(body, bg=COLOR_CANVAS)
        search_row.pack(fill=tk.X, pady=(0, 10))
        search_var = tk.StringVar()
        search_entry = tk.Entry(
            search_row,
            textvariable=search_var,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            selectbackground=COLOR_ACCENT_SUBTLE,
            relief=tk.FLAT,
            bd=0,
            font=(UI_FONT, 10),
        )
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)

        list_frame = tk.Frame(body, bg=COLOR_CANVAS)
        list_frame.pack(fill=tk.BOTH, expand=True)
        app_list = tk.Listbox(
            list_frame,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT_SECONDARY,
            selectbackground=COLOR_ACCENT_SUBTLE,
            selectforeground=COLOR_TEXT,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            bd=0,
            activestyle="none",
            font=(UI_FONT, 10),
        )
        scrollbar = ttk.Scrollbar(
            list_frame,
            orient=tk.VERTICAL,
            command=app_list.yview,
            style="Dark.Vertical.TScrollbar",
        )
        app_list.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        app_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        detail_label = tk.Label(
            body,
            text="選取程式後會在這裡顯示執行檔位置",
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9),
            anchor="w",
        )
        detail_label.pack(fill=tk.X, pady=(9, 0))
        status_label = tk.Label(
            body,
            text="",
            fg=COLOR_ACCENT,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9),
            anchor="w",
        )
        status_label.pack(fill=tk.X, pady=(3, 0))

        footer = tk.Frame(dialog, bg=COLOR_CHROME, height=62)
        footer.pack(fill=tk.X)
        footer.pack_propagate(False)

        applications: list[RunningApplication] = []
        visible_applications: list[RunningApplication] = []

        def refresh_results(*_args: object) -> None:
            query = search_var.get().strip().casefold()
            visible_applications.clear()
            visible_applications.extend(
                application
                for application in applications
                if not query
                or query in application.name.casefold()
                or query in application.title.casefold()
                or query in application.path.casefold()
            )
            app_list.delete(0, tk.END)
            for application in visible_applications:
                label = application.name
                if application.title.casefold() != application.name.casefold():
                    label += f"    ·    {application.title}"
                app_list.insert(tk.END, f"  {label}")
            status_label.configure(
                text=(
                    f"顯示 {len(visible_applications)} 個程式"
                    if visible_applications
                    else "沒有符合的執行中程式"
                )
            )
            detail_label.configure(text="選取程式後會在這裡顯示執行檔位置")
            if visible_applications:
                app_list.selection_set(0)
                app_list.activate(0)
                show_selection()

        def scan_applications() -> None:
            status_label.configure(text="正在讀取執行中的程式…")
            dialog.update_idletasks()
            try:
                applications[:] = list_running_applications()
            except (OSError, AttributeError) as error:
                applications.clear()
                messagebox.showerror(
                    "無法讀取執行中的程式",
                    str(error),
                    parent=dialog,
                )
            refresh_results()

        def show_selection(_event: tk.Event | None = None) -> None:
            selection = app_list.curselection()
            if not selection or selection[0] >= len(visible_applications):
                return
            detail_label.configure(text=visible_applications[selection[0]].path)

        def choose_application(_event: tk.Event | None = None) -> None:
            selection = app_list.curselection()
            if not selection or selection[0] >= len(visible_applications):
                return
            application = visible_applications[selection[0]]
            dialog.destroy()
            self._open_task_editor(seed_application=application)

        ttk.Button(
            search_row,
            text="重新整理",
            command=scan_applications,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            footer,
            text="建立任務",
            command=choose_application,
            bg=COLOR_ACCENT,
            fg=COLOR_CHROME,
            activebackground=COLOR_ACCENT_HOVER,
            activeforeground=COLOR_CHROME,
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=8,
            font=(UI_FONT, 10, "bold"),
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 20), pady=13)
        tk.Button(
            footer,
            text="取消",
            command=dialog.destroy,
            bg=COLOR_SURFACE_RAISED,
            fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_SURFACE_HOVER,
            activeforeground=COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=8,
            font=(UI_FONT, 10),
            cursor="hand2",
        ).pack(side=tk.RIGHT, pady=13)

        search_var.trace_add("write", refresh_results)
        app_list.bind("<<ListboxSelect>>", show_selection)
        app_list.bind("<Double-Button-1>", choose_application)
        dialog.bind("<Return>", choose_application)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.bind("<Control-f>", lambda _event: search_entry.focus_set())
        scan_applications()
        search_entry.focus_set()

    def edit_task(self, index: int | None = None) -> None:
        if index is None:
            index = self._selected_index()
        if index is None:
            messagebox.showwarning("提醒", "請選擇一個待辦事項", parent=self.root)
            return
        self._open_task_editor(index)

    def _open_task_editor(
        self,
        index: int | None = None,
        seed_application: RunningApplication | None = None,
    ) -> None:
        is_new = index is None
        if is_new:
            working_task = {
                "task": seed_application.name if seed_application else "",
                "group_id": UNGROUPED_ID,
                "completion_mode": DAILY,
                "last_completed_at": None,
                "quick_launch": (
                    [
                        {
                            "target": seed_application.path,
                            "args": [],
                            "focus_existing": True,
                        }
                    ]
                    if seed_application
                    else []
                ),
            }
        else:
            working_task = copy.deepcopy(self.data["tasks"][index])

        dialog = tk.Toplevel(self.root)
        dialog.title("新增任務" if is_new else "編輯任務")
        dialog.geometry(self._dialog_geometry(620, 580))
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg=COLOR_CANVAS)

        header = tk.Frame(dialog, bg=COLOR_CHROME, height=64)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="新增任務" if is_new else "編輯任務",
            fg=COLOR_TEXT,
            bg=COLOR_CHROME,
            font=(UI_FONT_DISPLAY, 17, "bold"),
        ).pack(side=tk.LEFT, padx=20, fill=tk.Y)

        content = tk.Frame(dialog, bg=COLOR_CANVAS)
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)

        tk.Label(
            content,
            text="任務名稱",
            fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9, "bold"),
        ).grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=working_task["task"])
        name_entry = tk.Entry(
            content,
            textvariable=name_var,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT,
            selectbackground=COLOR_ACCENT_SUBTLE,
            relief=tk.FLAT,
            bd=0,
            font=(UI_FONT, 11),
        )
        name_entry.grid(row=1, column=0, sticky="ew", ipady=9, pady=(5, 14))

        tk.Label(
            content,
            text="完成模式",
            fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9, "bold"),
        ).grid(row=0, column=1, sticky="w", padx=(14, 0))
        mode_var = tk.StringVar(
            value="每日重置"
            if working_task["completion_mode"] == DAILY
            else "永久保留"
        )
        mode_box = ttk.Combobox(
            content,
            textvariable=mode_var,
            values=("每日重置", "永久保留"),
            state="readonly",
            width=14,
            style="Dark.TCombobox",
        )
        mode_box.grid(row=1, column=1, sticky="ew", padx=(14, 0), pady=(5, 14))

        tk.Label(
            content,
            text="所屬群組",
            fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9, "bold"),
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        group_names = [
            "依規則自動分類",
            "未分類",
            *(group["name"] for group in self.data["groups"]),
        ]
        group_name_to_id = {
            "依規則自動分類": AUTO_GROUP_ID,
            "未分類": UNGROUPED_ID,
            **{group["name"]: group["id"] for group in self.data["groups"]},
        }
        stored_group_id = working_task.get("group_id", UNGROUPED_ID)
        current_group = (
            "依規則自動分類"
            if stored_group_id == AUTO_GROUP_ID
            else next(
                (
                    group["name"]
                    for group in self.data["groups"]
                    if group["id"] == stored_group_id
                ),
                "未分類",
            )
        )
        group_var = tk.StringVar(value=current_group)
        group_box = ttk.Combobox(
            content,
            textvariable=group_var,
            values=group_names,
            state="readonly",
            style="Dark.TCombobox",
        )
        group_box.grid(
            row=3, column=0, columnspan=2, sticky="ew", ipady=1, pady=(5, 14)
        )

        section_row = tk.Frame(content, bg=COLOR_CANVAS)
        section_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        tk.Label(
            section_row,
            text="啟動項目",
            fg=COLOR_TEXT,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 11, "bold"),
        ).pack(side=tk.LEFT)
        launch_count_label = tk.Label(
            section_row,
            fg=COLOR_ACCENT,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9),
        )
        launch_count_label.pack(side=tk.RIGHT)

        launch_list = tk.Listbox(
            content,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT_SECONDARY,
            selectbackground=COLOR_ACCENT_SUBTLE,
            selectforeground=COLOR_TEXT,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            bd=0,
            font=(UI_FONT, 9),
            height=8,
        )
        launch_list.grid(row=5, column=0, columnspan=2, sticky="nsew")
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0)
        content.grid_rowconfigure(5, weight=1)

        def refresh_launches(selected: int | None = None) -> None:
            launch_list.delete(0, tk.END)
            for item in working_task["quick_launch"]:
                target = Path(item["target"])
                args = subprocess.list2cmdline(item.get("args", []))
                detail = f"  {args}" if args else ""
                behavior = "↩ 已開啟則切回   " if item.get("focus_existing") else ""
                launch_list.insert(
                    tk.END,
                    f"  {behavior}{target.name}{detail}    —    {target.parent}",
                )
            launch_count_label.configure(
                text=f"{len(working_task['quick_launch'])} 個項目"
            )
            if selected is not None and working_task["quick_launch"]:
                selected = min(selected, len(working_task["quick_launch"]) - 1)
                launch_list.selection_set(selected)

        def add_launchers() -> None:
            paths = filedialog.askopenfilenames(
                title="選擇一個或多個啟動項目",
                filetypes=[
                    ("可啟動項目", "*.exe *.com *.bat *.cmd *.lnk *.url"),
                    ("所有文件", "*.*"),
                ],
                parent=dialog,
            )
            for path in paths:
                if not any(item["target"] == path for item in working_task["quick_launch"]):
                    working_task["quick_launch"].append({"target": path, "args": []})
            if paths:
                refresh_launches(len(working_task["quick_launch"]) - 1)

        def edit_arguments() -> None:
            selection = launch_list.curselection()
            if not selection:
                return
            launch_index = selection[0]
            item = working_task["quick_launch"][launch_index]
            current = subprocess.list2cmdline(item.get("args", []))
            value = simpledialog.askstring(
                "啟動參數",
                "啟動參數（含空白的參數請加引號）：",
                initialvalue=current,
                parent=dialog,
            )
            if value is not None:
                normalized = normalize_launch_item(
                    {
                        "target": item["target"],
                        "args": value,
                        "cwd": item.get("cwd"),
                        "focus_existing": item.get("focus_existing", False),
                    }
                )
                if normalized:
                    working_task["quick_launch"][launch_index] = normalized
                    refresh_launches(launch_index)

        def remove_launcher() -> None:
            selection = launch_list.curselection()
            if not selection:
                return
            launch_index = selection[0]
            del working_task["quick_launch"][launch_index]
            refresh_launches(launch_index)

        def toggle_focus_existing() -> None:
            selection = launch_list.curselection()
            if not selection:
                return
            launch_index = selection[0]
            item = working_task["quick_launch"][launch_index]
            suffix = Path(item["target"]).suffix.lower()
            if suffix not in {".exe", ".com"}:
                messagebox.showinfo(
                    "切回既有視窗",
                    "這個選項目前只支援直接選取的 EXE 或 COM 程式。",
                    parent=dialog,
                )
                return
            item["focus_existing"] = not item.get("focus_existing", False)
            refresh_launches(launch_index)

        launch_controls = tk.Frame(content, bg=COLOR_CANVAS)
        launch_controls.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(
            launch_controls,
            text="＋ 選擇檔案",
            command=add_launchers,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT)
        ttk.Button(
            launch_controls,
            text="編輯參數",
            command=edit_arguments,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            launch_controls,
            text="已開啟則切回",
            command=toggle_focus_existing,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            launch_controls,
            text="移除",
            command=remove_launcher,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT)

        footer = tk.Frame(dialog, bg=COLOR_CHROME, height=62)
        footer.pack(fill=tk.X)
        footer.pack_propagate(False)

        def save_changes() -> None:
            name = name_var.get().strip()
            if not name:
                messagebox.showwarning("提醒", "請輸入任務名稱", parent=dialog)
                name_entry.focus_set()
                return
            working_task["task"] = name
            working_task["group_id"] = group_name_to_id.get(
                group_var.get(), UNGROUPED_ID
            )
            working_task["completion_mode"] = (
                DAILY if mode_var.get() == "每日重置" else PERMANENT
            )
            if is_new:
                self.data["tasks"].append(working_task)
                saved_index = len(self.data["tasks"]) - 1
            else:
                original = self.data["tasks"][index]
                self.data["tasks"][index] = working_task
                saved_index = index
            if self._save():
                self.update_listbox(saved_index)
                dialog.destroy()
                self.show_toast("任務已建立" if is_new else "任務已更新")
            elif is_new:
                self.data["tasks"].pop()
            else:
                self.data["tasks"][index] = original

        tk.Button(
            footer,
            text="儲存任務",
            command=save_changes,
            bg=COLOR_ACCENT,
            fg=COLOR_CHROME,
            activebackground=COLOR_ACCENT_HOVER,
            activeforeground=COLOR_CHROME,
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=8,
            font=(UI_FONT, 10, "bold"),
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 20), pady=13)
        tk.Button(
            footer,
            text="取消",
            command=dialog.destroy,
            bg=COLOR_SURFACE_RAISED,
            fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_SURFACE_HOVER,
            activeforeground=COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=8,
            font=(UI_FONT, 10),
            cursor="hand2",
        ).pack(side=tk.RIGHT, pady=13)

        refresh_launches()
        name_entry.focus_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("設定")
        dialog.geometry(self._dialog_geometry(700, 680))
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg=COLOR_CANVAS)

        header = tk.Frame(dialog, bg=COLOR_CHROME, height=58)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="偏好設定",
            fg=COLOR_TEXT,
            bg=COLOR_CHROME,
            font=(UI_FONT_DISPLAY, 16, "bold"),
        ).pack(side=tk.LEFT, padx=18, fill=tk.Y)

        auto_hide = tk.BooleanVar(
            value=self.data["settings"].get("auto_hide_after_launch", True)
        )
        density_labels = {"舒適": "comfortable", "緊湊": "compact"}
        density_var = tk.StringVar(
            value="緊湊"
            if self.data["settings"].get("density") == "compact"
            else "舒適"
        )
        current_width = self.data["settings"].get("panel_width", 460)
        width_var = tk.StringVar(
            value=next(
                (
                    label
                    for label, value in PANEL_WIDTHS.items()
                    if value == current_width
                ),
                "標準 · 460",
            )
        )
        show_details = tk.BooleanVar(
            value=self.data["settings"].get("show_task_details", True)
        )
        working_groups = copy.deepcopy(self.data["groups"])
        setting_area = tk.Frame(dialog, bg=COLOR_CANVAS)
        setting_area.pack(fill=tk.BOTH, expand=True, padx=20, pady=14)

        tk.Label(
            setting_area,
            text="外觀與版面",
            fg=COLOR_TEXT,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 11, "bold"),
        ).pack(anchor="w")
        appearance_row = tk.Frame(setting_area, bg=COLOR_CANVAS)
        appearance_row.pack(fill=tk.X, pady=(8, 10))
        for title, variable, values, width in (
            ("資訊密度", density_var, tuple(density_labels), 10),
            ("面板寬度", width_var, tuple(PANEL_WIDTHS), 14),
        ):
            field = tk.Frame(appearance_row, bg=COLOR_CANVAS)
            field.pack(side=tk.LEFT, padx=(0, 14))
            tk.Label(
                field, text=title, fg=COLOR_TEXT_SECONDARY, bg=COLOR_CANVAS,
                font=(UI_FONT, 9, "bold"),
            ).pack(anchor="w", pady=(0, 4))
            ttk.Combobox(
                field, textvariable=variable, values=values, state="readonly",
                width=width, style="Dark.TCombobox",
            ).pack()
        details_field = tk.Frame(appearance_row, bg=COLOR_CANVAS)
        details_field.pack(side=tk.LEFT, fill=tk.Y)
        tk.Label(
            details_field, text="卡片資訊", fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_CANVAS, font=(UI_FONT, 9, "bold"),
        ).pack(anchor="w", pady=(0, 4))
        ttk.Checkbutton(
            details_field, text="顯示啟動數與完成時間", variable=show_details,
        ).pack(anchor="w")
        ttk.Checkbutton(
            setting_area,
            text="成功啟動後自動收合面板",
            variable=auto_hide,
        ).pack(anchor="w")
        tk.Label(
            setting_area,
            text="關閉後，啟動完成時面板會維持展開。",
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9),
        ).pack(anchor="w", padx=(24, 0), pady=(4, 0))

        tk.Frame(setting_area, bg=COLOR_BORDER, height=1).pack(
            fill=tk.X, pady=(14, 12)
        )
        group_heading = tk.Frame(setting_area, bg=COLOR_CANVAS)
        group_heading.pack(fill=tk.X)
        tk.Label(
            group_heading,
            text="任務群組",
            fg=COLOR_TEXT,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            group_heading,
            text="顯示順序由上到下",
            fg=COLOR_TEXT_MUTED,
            bg=COLOR_CANVAS,
            font=(UI_FONT, 9),
        ).pack(side=tk.RIGHT)

        group_area = tk.Frame(setting_area, bg=COLOR_CANVAS)
        group_area.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        group_list = tk.Listbox(
            group_area,
            width=24,
            bg=COLOR_SURFACE,
            fg=COLOR_TEXT_SECONDARY,
            selectbackground=COLOR_ACCENT_SUBTLE,
            selectforeground=COLOR_TEXT,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
            bd=0,
            font=(UI_FONT, 10),
            activestyle="none",
        )
        group_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        editor = tk.Frame(group_area, bg=COLOR_SURFACE_RAISED, width=330)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, padx=(12, 0))
        editor.pack_propagate(False)
        tk.Label(
            editor, text="群組名稱", fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_SURFACE_RAISED, font=(UI_FONT, 9, "bold"),
        ).pack(anchor="w", padx=14, pady=(14, 5))
        group_name = tk.StringVar()
        group_entry = tk.Entry(
            editor, textvariable=group_name, bg=COLOR_SURFACE, fg=COLOR_TEXT,
            insertbackground=COLOR_TEXT, selectbackground=COLOR_ACCENT_SUBTLE,
            relief=tk.FLAT, bd=0, font=(UI_FONT, 11),
        )
        group_entry.pack(fill=tk.X, padx=14, ipady=8)
        identity_row = tk.Frame(editor, bg=COLOR_SURFACE_RAISED)
        identity_row.pack(fill=tk.X, padx=14, pady=(12, 0))
        icon_field = tk.Frame(identity_row, bg=COLOR_SURFACE_RAISED)
        icon_field.pack(side=tk.LEFT, anchor="n")
        tk.Label(
            icon_field, text="圖示／符號", fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_SURFACE_RAISED, font=(UI_FONT, 9, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        group_icon = tk.StringVar()
        icon_entry = tk.Entry(
            icon_field, textvariable=group_icon, width=8, bg=COLOR_SURFACE,
            fg=COLOR_TEXT, insertbackground=COLOR_TEXT,
            selectbackground=COLOR_ACCENT_SUBTLE,
            relief=tk.FLAT, bd=0, font=("Segoe UI Emoji", 11), justify=tk.CENTER,
        )
        icon_entry.pack(ipady=7)
        color_field = tk.Frame(identity_row, bg=COLOR_SURFACE_RAISED)
        color_field.pack(side=tk.LEFT, anchor="n", padx=(14, 0))
        tk.Label(
            color_field, text="強調色", fg=COLOR_TEXT_SECONDARY,
            bg=COLOR_SURFACE_RAISED, font=(UI_FONT, 9, "bold"),
        ).pack(anchor="w", pady=(0, 5))
        group_color = tk.StringVar()
        color_controls = tk.Frame(color_field, bg=COLOR_SURFACE_RAISED)
        color_controls.pack()
        color_box = ttk.Combobox(
            color_controls, textvariable=group_color, values=tuple(GROUP_COLORS),
            state="readonly", width=12, style="Dark.TCombobox",
        )
        color_box.pack(side=tk.LEFT)
        color_swatch = tk.Label(
            color_controls, bg="#78a9ff", width=2, relief=tk.FLAT, bd=0,
        )
        color_swatch.pack(side=tk.LEFT, fill=tk.Y, padx=(5, 0))

        def resolved_group_color() -> str:
            candidate = GROUP_COLORS.get(group_color.get(), group_color.get())
            if len(candidate) == 7 and candidate.startswith("#"):
                try:
                    int(candidate[1:], 16)
                    return candidate
                except ValueError:
                    pass
            return "#78a9ff"

        def refresh_color_swatch(_event: tk.Event | None = None) -> None:
            color_swatch.configure(bg=resolved_group_color())

        def choose_group_color() -> None:
            _rgb, selected = colorchooser.askcolor(
                color=resolved_group_color(), title="選擇群組色彩", parent=dialog
            )
            if selected:
                group_color.set(selected)
                refresh_color_swatch()

        color_box.bind("<<ComboboxSelected>>", refresh_color_swatch)
        tk.Button(
            color_field, text="自訂色…", command=choose_group_color,
            bg=COLOR_SURFACE_HOVER, fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_BORDER_STRONG, activeforeground=COLOR_TEXT,
            relief=tk.FLAT, bd=0, padx=8, pady=4,
            font=(UI_FONT, 8), cursor="hand2",
        ).pack(anchor="w", pady=(6, 0))
        behavior_row = tk.Frame(editor, bg=COLOR_SURFACE_RAISED)
        behavior_row.pack(fill=tk.X, padx=10, pady=(10, 0))
        group_batch = tk.BooleanVar()
        ttk.Checkbutton(
            behavior_row,
            text="整組啟動",
            variable=group_batch,
            style="Group.TCheckbutton",
        ).pack(side=tk.LEFT)
        group_rule = tk.StringVar(value="手動內容")
        ttk.Combobox(
            behavior_row,
            textvariable=group_rule,
            values=tuple(GROUP_RULE_LABELS),
            state="readonly",
            width=15,
            style="Dark.TCombobox",
        ).pack(side=tk.RIGHT)

        editor_index: int | None = None

        def refresh_groups(selected: int | None = None) -> None:
            group_list.delete(0, tk.END)
            for group in working_groups:
                details = []
                if group["batch_launch"]:
                    details.append("整組啟動")
                if group.get("rule", "manual") != "manual":
                    details.append("自動")
                suffix = f"   ·   {' / '.join(details)}" if details else ""
                group_list.insert(
                    tk.END, f"  {group['icon']}  {group['name']}{suffix}"
                )
            if selected is not None and working_groups:
                selected = max(0, min(selected, len(working_groups) - 1))
                group_list.selection_set(selected)
                group_list.activate(selected)

        def load_group(index: int) -> None:
            nonlocal editor_index
            editor_index = index
            group_name.set(working_groups[index]["name"])
            group_icon.set(working_groups[index].get("icon", "●"))
            color_value = working_groups[index].get("color", "#78a9ff")
            group_color.set(
                next(
                    (name for name, value in GROUP_COLORS.items() if value == color_value),
                    color_value,
                )
            )
            refresh_color_swatch()
            group_batch.set(working_groups[index]["batch_launch"])
            rule_value = working_groups[index].get("rule", "manual")
            group_rule.set(
                next(
                    (
                        label
                        for label, value in GROUP_RULE_LABELS.items()
                        if value == rule_value
                    ),
                    "手動內容",
                )
            )

        def apply_editor(show_warning: bool = True) -> bool:
            if editor_index is None:
                return True
            name = group_name.get().strip()
            duplicate = any(
                index != editor_index and group["name"].casefold() == name.casefold()
                for index, group in enumerate(working_groups)
            )
            if not name or duplicate:
                if show_warning:
                    messagebox.showwarning(
                        "群組名稱",
                        "請輸入不重複的群組名稱。",
                        parent=dialog,
                    )
                    group_entry.focus_set()
                return False
            working_groups[editor_index]["name"] = name
            working_groups[editor_index]["icon"] = group_icon.get().strip()[:4] or "●"
            working_groups[editor_index]["color"] = resolved_group_color()
            working_groups[editor_index]["batch_launch"] = group_batch.get()
            working_groups[editor_index]["rule"] = GROUP_RULE_LABELS[
                group_rule.get()
            ]
            return True

        def select_group(_event: tk.Event | None = None) -> None:
            selection = group_list.curselection()
            if not selection:
                return
            new_index = selection[0]
            if new_index == editor_index:
                return
            if not apply_editor():
                group_list.selection_clear(0, tk.END)
                if editor_index is not None:
                    group_list.selection_set(editor_index)
                return
            refresh_groups(new_index)
            load_group(new_index)

        def add_group() -> None:
            nonlocal editor_index
            if not apply_editor():
                return
            existing = {group["name"] for group in working_groups}
            number = 1
            name = "新群組"
            while name in existing:
                number += 1
                name = f"新群組 {number}"
            working_groups.append(
                {
                    "id": uuid.uuid4().hex,
                    "name": name,
                    "icon": "●",
                    "color": "#78a9ff",
                    "batch_launch": False,
                    "collapsed": False,
                    "rule": "manual",
                }
            )
            refresh_groups(len(working_groups) - 1)
            load_group(len(working_groups) - 1)
            group_entry.focus_set()
            group_entry.selection_range(0, tk.END)

        def delete_group() -> None:
            nonlocal editor_index
            if editor_index is None:
                return
            name = working_groups[editor_index]["name"]
            if not messagebox.askyesno(
                "刪除群組",
                f"刪除「{name}」？其中的任務會移到「未分類」。",
                parent=dialog,
            ):
                return
            del working_groups[editor_index]
            editor_index = None
            group_name.set("")
            group_icon.set("")
            group_color.set("天空藍")
            group_batch.set(False)
            next_index = min(len(working_groups) - 1, group_list.curselection()[0]) if working_groups else None
            refresh_groups(next_index)
            if next_index is not None:
                load_group(next_index)

        def move_group(offset: int) -> None:
            nonlocal editor_index
            if editor_index is None or not apply_editor():
                return
            target = editor_index + offset
            if not 0 <= target < len(working_groups):
                return
            working_groups[editor_index], working_groups[target] = (
                working_groups[target], working_groups[editor_index]
            )
            editor_index = target
            refresh_groups(target)

        group_list.bind("<<ListboxSelect>>", select_group)
        group_buttons = tk.Frame(setting_area, bg=COLOR_CANVAS)
        group_buttons.pack(fill=tk.X, pady=(9, 0))
        for text, command in (
            ("＋ 新增", add_group),
            ("刪除", delete_group),
            ("↑ 上移", lambda: move_group(-1)),
            ("↓ 下移", lambda: move_group(1)),
        ):
            ttk.Button(
                group_buttons, text=text, command=command, style="Secondary.TButton"
            ).pack(side=tk.LEFT, padx=(0, 6))

        refresh_groups(0 if working_groups else None)
        if working_groups:
            load_group(0)

        def save_settings() -> None:
            if not apply_editor():
                return
            valid_group_ids = {group["id"] for group in working_groups}
            previous_groups = self.data["groups"]
            previous_settings = copy.deepcopy(self.data["settings"])
            previous_task_groups = [task.get("group_id", UNGROUPED_ID) for task in self.data["tasks"]]
            self.data["settings"]["auto_hide_after_launch"] = auto_hide.get()
            self.data["settings"]["density"] = density_labels[density_var.get()]
            self.data["settings"]["panel_width"] = PANEL_WIDTHS[width_var.get()]
            self.data["settings"]["show_task_details"] = show_details.get()
            self.data["groups"] = working_groups
            for task in self.data["tasks"]:
                if task.get("group_id") not in {*valid_group_ids, AUTO_GROUP_ID}:
                    task["group_id"] = UNGROUPED_ID
            if self._save():
                dialog.destroy()
                self._apply_panel_width()
                self.update_listbox()
                self.show_toast("設定已儲存")
            else:
                self.data["groups"] = previous_groups
                self.data["settings"] = previous_settings
                for task, group_id in zip(self.data["tasks"], previous_task_groups):
                    task["group_id"] = group_id

        controls = tk.Frame(dialog, bg=COLOR_CHROME, height=58)
        controls.pack(fill=tk.X)
        controls.pack_propagate(False)
        tk.Button(
            controls,
            text="儲存設定",
            command=save_settings,
            bg=COLOR_ACCENT,
            fg=COLOR_CHROME,
            activebackground=COLOR_ACCENT_HOVER,
            activeforeground=COLOR_CHROME,
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=7,
            font=(UI_FONT, 9, "bold"),
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 16), pady=12)
        tk.Button(
            controls,
            text="取消",
            command=dialog.destroy,
            bg=COLOR_SURFACE_RAISED,
            fg=COLOR_TEXT_SECONDARY,
            activebackground=COLOR_SURFACE_HOVER,
            activeforeground=COLOR_TEXT,
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=7,
            font=(UI_FONT, 9),
            cursor="hand2",
        ).pack(side=tk.RIGHT, pady=12)

    def delete_task(self, index: int | None = None) -> None:
        if index is None:
            index = self._selected_index()
        if index is None:
            messagebox.showwarning("提醒", "請選擇一個待辦事項", parent=self.root)
            return
        name = self.data["tasks"][index]["task"]
        if not messagebox.askyesno("確認刪除", f"確定要刪除「{name}」嗎？", parent=self.root):
            return
        del self.data["tasks"][index]
        self.failed_task_indices = {
            failed_index - 1 if failed_index > index else failed_index
            for failed_index in self.failed_task_indices
            if failed_index != index
        }
        if self._save():
            self.update_listbox(index)

    def toggle_done(self, index: int | None = None) -> None:
        if index is None:
            index = self._selected_index()
        if index is None:
            messagebox.showwarning("提醒", "請選擇一個待辦事項", parent=self.root)
            return
        task = self.data["tasks"][index]
        set_task_complete(task, not is_task_complete(task))
        if self._save():
            self.update_listbox(index)

    def manage_launchers(self, index: int | None = None) -> None:
        if index is None:
            index = self._selected_index()
        if index is None:
            messagebox.showwarning("提醒", "請選擇一個待辦事項", parent=self.root)
            return
        self._open_task_editor(index)

    def launch_task(self, index: int) -> None:
        self.selected_task_index = index
        self.launch_selected()

    def launch_group(self, group_id: str) -> None:
        if self.launching:
            return
        indices = [
            index
            for index, task in enumerate(self.data["tasks"])
            if self._effective_group_id(task) == group_id
            and not is_task_complete(task)
        ]
        if not indices:
            self.show_toast("這個群組已全部完成")
            return

        self.launching = True
        completed = 0
        error_messages: list[str] = []
        try:
            for index in indices:
                task = self.data["tasks"][index]
                self.launching_task_index = index
                self.failed_task_indices.discard(index)
                self.update_listbox(index)
                self.root.update_idletasks()
                if not task["quick_launch"]:
                    self.failed_task_indices.add(index)
                    error_messages.append(f"{task['task']}：尚未設定啟動程式")
                    continue
                task_errors = []
                for item in task["quick_launch"]:
                    try:
                        launch(item)
                    except LaunchError as error:
                        task_errors.append(str(error))
                if task_errors:
                    self.failed_task_indices.add(index)
                    error_messages.append(f"{task['task']}：{'；'.join(task_errors)}")
                    continue
                set_task_complete(task, True)
                completed += 1

            if completed and not self._save():
                return
            if error_messages:
                messagebox.showerror(
                    "群組中有項目啟動失敗",
                    "\n\n".join(error_messages),
                    parent=self.root,
                )
            elif completed:
                self.show_toast(f"已啟動 {completed} 個任務，並標記完成")
                if self.data["settings"].get("auto_hide_after_launch", True):
                    self.root.after(900, self.hide_window)
        finally:
            self.launching = False
            self.launching_task_index = None
            self.update_listbox()

    def launch_selected(self) -> None:
        if self.launching:
            return
        index = self._selected_index()
        if index is None:
            messagebox.showwarning("提醒", "請選擇一個待辦事項", parent=self.root)
            return
        launch_items = self.data["tasks"][index]["quick_launch"]
        if not launch_items:
            messagebox.showwarning("提醒", "此項目尚未設定啟動程式", parent=self.root)
            return
        task = self.data["tasks"][index]
        was_complete = is_task_complete(task)
        self.launching = True
        self.launching_task_index = index
        self.failed_task_indices.discard(index)
        self.update_listbox(index)
        self.root.update_idletasks()

        errors = []
        try:
            for item in launch_items:
                try:
                    launch(item)
                except LaunchError as error:
                    errors.append(str(error))
            if errors:
                self.failed_task_indices.add(index)
                messagebox.showerror(
                    "部分項目啟動失敗", "\n\n".join(errors), parent=self.root
                )
                return

            if not was_complete:
                set_task_complete(task, True)
                if not self._save():
                    return
            message = (
                f"已重新啟動 {len(launch_items)} 個項目"
                if was_complete
                else f"已啟動 {len(launch_items)} 個項目，今日完成"
            )
            self.show_toast(message)
            if self.data["settings"].get("auto_hide_after_launch", True):
                self.root.after(900, self.hide_window)
        finally:
            self.launching = False
            self.launching_task_index = None
            self.update_listbox(index)

    def show_toast(self, message: str, duration_ms: int = 2600) -> None:
        if self.toast_window is not None and self.toast_window.winfo_exists():
            self.toast_window.destroy()

        toast = tk.Toplevel(self.root)
        self.toast_window = toast
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=COLOR_SURFACE_RAISED)
        tk.Label(
            toast,
            text=message,
            bg=COLOR_SURFACE_RAISED,
            fg=COLOR_TEXT,
            padx=16,
            pady=10,
            font=(UI_FONT, 9),
        ).pack()
        toast.update_idletasks()
        x = self.edge_x - toast.winfo_reqwidth() - 16
        y = self.panel_y + self.window_height - toast.winfo_reqheight() - 16
        toast.geometry(f"+{x}+{y}")
        toast.after(duration_ms, toast.destroy)

    def _start_move(self, event: tk.Event) -> None:
        self.drag_x = event.x
        self.drag_y = event.y

    def _move_app(self, event: tk.Event) -> None:
        x = self.root.winfo_x() + event.x - self.drag_x
        y = self.root.winfo_y() + event.y - self.drag_y
        self.root.geometry(f"+{x}+{y}")

    def show_window(self) -> None:
        self.root.geometry(
            f"{self.window_width}x{self.window_height}+{self.panel_x}+{self.panel_y}"
        )
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.visible = True
        arrow_x = self.panel_x - self.arrow_width
        self.arrow_window.geometry(
            f"{self.arrow_width}x{self.arrow_height}+{arrow_x}+{self.panel_y}"
        )
        self._set_arrow(expanded=True)

    def hide_window(self) -> None:
        self.root.withdraw()
        self.visible = False
        arrow_x = self.edge_x - self.arrow_width
        arrow_y = self.panel_y + (self.window_height - self.arrow_height) // 2
        self.arrow_window.geometry(
            f"{self.arrow_width}x{self.arrow_height}+{arrow_x}+{arrow_y}"
        )
        self._set_arrow(expanded=False)

    def toggle_window(self) -> None:
        if self.visible:
            self.hide_window()
        else:
            self.show_window()

    def _start_tray(self) -> None:
        try:
            from PIL import Image
            from pystray import Icon, Menu, MenuItem

            image = Image.open(resource_path("icon.png"))
            menu = Menu(
                MenuItem("顯示視窗", lambda _icon, _item: self.actions.put("show"), default=True),
                MenuItem("退出", lambda _icon, _item: self.actions.put("exit")),
            )
            self.tray_icon = Icon(APP_NAME, image, APP_NAME, menu)
            self.tray_thread = threading.Thread(target=self.tray_icon.run, daemon=True)
            self.tray_thread.start()
        except (ImportError, OSError) as error:
            error_message = str(error)
            self.root.after(
                200,
                lambda message=error_message: messagebox.showwarning(
                    "系統匣停用", f"無法建立系統匣圖示：{message}", parent=self.root
                ),
            )

    def _poll_actions(self) -> None:
        try:
            while True:
                action = self.actions.get_nowait()
                if action == "show":
                    self.show_window()
                elif action == "exit":
                    self.exit_app()
                    return
        except queue.Empty:
            pass
        self.root.after(100, self._poll_actions)

    def exit_app(self) -> None:
        if self.tray_icon is not None:
            self.tray_icon.stop()
        self.root.destroy()


def main() -> int:
    instance = SingleInstance()
    if not instance.acquire():
        if os.name == "nt":
            ctypes.windll.user32.MessageBoxW(
                None, "TodoLauncher 已經在執行中。", APP_NAME, 0x40
            )
        return 0

    try:
        root = tk.Tk()
        root.withdraw()
        store = TaskStore(user_data_path(), legacy_path=application_dir() / "tasks.json")
        TodoApp(root, store)
        root.mainloop()
        return 0
    finally:
        instance.release()


if __name__ == "__main__":
    raise SystemExit(main())
