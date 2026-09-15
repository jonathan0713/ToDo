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
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any

from launcher import LaunchError, launch
from task_store import (
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
        self.root.configure(bg="#17191d")
        self.root.protocol("WM_DELETE_WINDOW", self.hide_window)

        left, top, right, bottom = self._work_area()
        self.work_left = left
        self.work_top = top
        self.work_right = right
        self.work_bottom = bottom
        work_width, work_height = right - left, bottom - top
        self.window_width = min(max(440, int(work_width * 0.24)), 560)
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
            background="#292d34",
            foreground="#f2f4f8",
            bordercolor="#292d34",
            borderwidth=0,
            relief="flat",
            padding=(14, 8),
            font=("Segoe UI", 10),
        )
        style.map(
            "Secondary.TButton",
            background=[("active", "#343943")],
            foreground=[("disabled", "#737984")],
        )
        style.configure(
            "TCheckbutton",
            background="#202329",
            foreground="#f2f4f8",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Group.TCheckbutton",
            background="#292d34",
            foreground="#f2f4f8",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Daily.Horizontal.TProgressbar",
            troughcolor="#343943",
            background="#4b82e6",
            bordercolor="#343943",
            lightcolor="#4b82e6",
            darkcolor="#4b82e6",
            thickness=6,
        )
        style.configure(
            "Dark.Vertical.TScrollbar",
            troughcolor="#202329",
            background="#464d59",
            bordercolor="#202329",
            arrowcolor="#aeb5c0",
            lightcolor="#464d59",
            darkcolor="#464d59",
            width=9,
        )
        style.configure(
            "Dark.TCombobox",
            fieldbackground="#17191d",
            background="#292d34",
            foreground="#f4f6fa",
            arrowcolor="#aeb5c0",
            bordercolor="#3a414d",
            padding=6,
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#17191d")],
            foreground=[("readonly", "#f4f6fa")],
            selectbackground=[("readonly", "#17191d")],
            selectforeground=[("readonly", "#f4f6fa")],
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

    def _build_ui(self) -> None:
        title_bar = tk.Frame(self.root, bg="#17191d", height=46)
        title_bar.pack(fill=tk.X)
        title_bar.pack_propagate(False)
        title_bar.bind("<Button-1>", self._start_move)
        title_bar.bind("<B1-Motion>", self._move_app)

        title_label = tk.Label(
            title_bar,
            text="TODAY",
            fg="#8b95a5",
            bg="#17191d",
            anchor="w",
            font=("Segoe UI Semibold", 9),
        )
        title_label.pack(side=tk.LEFT, padx=18, fill=tk.Y)
        title_label.bind("<Button-1>", self._start_move)
        title_label.bind("<B1-Motion>", self._move_app)

        tk.Button(
            title_bar,
            text="×",
            command=self.hide_window,
            fg="#aeb5c0",
            bg="#17191d",
            activebackground="#292d34",
            activeforeground="white",
            bd=0,
            width=5,
            font=("Segoe UI", 13),
            cursor="hand2",
        ).pack(side=tk.RIGHT, fill=tk.Y)

        body = tk.Frame(self.root, bg="#202329")
        body.pack(fill=tk.BOTH, expand=True)

        summary = tk.Frame(body, bg="#202329")
        summary.pack(fill=tk.X, padx=20, pady=(16, 12))
        self.heading_label = tk.Label(
            summary,
            text="今日啟動",
            fg="#f4f6fa",
            bg="#202329",
            font=("Segoe UI Semibold", 22),
            anchor="w",
        )
        self.heading_label.pack(anchor="w")
        summary_row = tk.Frame(summary, bg="#202329")
        summary_row.pack(fill=tk.X, pady=(4, 8))
        self.date_label = tk.Label(
            summary_row,
            fg="#929aa7",
            bg="#202329",
            font=("Segoe UI", 10),
        )
        self.date_label.pack(side=tk.LEFT)
        self.progress_label = tk.Label(
            summary_row,
            fg="#78a9ff",
            bg="#202329",
            font=("Segoe UI Semibold", 10),
        )
        self.progress_label.pack(side=tk.RIGHT)
        self.progress = ttk.Progressbar(
            summary,
            maximum=100,
            mode="determinate",
            style="Daily.Horizontal.TProgressbar",
        )
        self.progress.pack(fill=tk.X)

        list_shell = tk.Frame(body, bg="#202329")
        list_shell.pack(fill=tk.BOTH, expand=True, padx=(14, 8))
        self.cards_canvas = tk.Canvas(
            list_shell,
            bg="#202329",
            highlightthickness=0,
            bd=0,
        )
        scrollbar = ttk.Scrollbar(
            list_shell,
            orient=tk.VERTICAL,
            command=self.cards_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.cards_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.cards_canvas.configure(yscrollcommand=scrollbar.set)
        self.cards_frame = tk.Frame(self.cards_canvas, bg="#202329")
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

        footer = tk.Frame(body, bg="#202329")
        footer.pack(fill=tk.X, padx=20, pady=(12, 18))
        tk.Button(
            footer,
            text="＋  新增任務",
            command=self.add_task,
            bg="#3b76d8",
            fg="white",
            activebackground="#4b86e8",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=8,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            footer,
            text="設定",
            command=self.open_settings,
            bg="#292d34",
            fg="#d6dae1",
            activebackground="#343943",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=8,
            font=("Segoe UI", 10),
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
            bg="#202329",
            fg="#aeb5c0",
            activebackground="#2f343d",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            font=("Segoe UI Light", 24),
            cursor="hand2",
        )
        self.arrow_button.pack(fill=tk.BOTH, expand=True)
        self.arrow_menu = tk.Menu(self.arrow_window, tearoff=False)
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

    def update_listbox(self, selected_index: int | None = None) -> None:
        if selected_index is not None:
            self.selected_task_index = selected_index
        elif not hasattr(self, "selected_task_index"):
            self.selected_task_index = None
        for child in self.cards_frame.winfo_children():
            child.destroy()

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
            "batch_launch": False,
        }]
        for group in display_groups:
            group_indices = [
                task_index
                for task_index, task in enumerate(tasks)
                if task.get("group_id", UNGROUPED_ID) == group["id"]
            ]
            if not group_indices:
                continue
            group_indices.sort(
                key=lambda task_index: (is_task_complete(tasks[task_index]), task_index)
            )
            self.visible_task_indices.extend(group_indices)
            group_complete = sum(is_task_complete(tasks[index]) for index in group_indices)
            section = tk.Frame(self.cards_frame, bg="#202329")
            section.pack(fill=tk.X, padx=(8, 10), pady=(12, 3))
            tk.Label(
                section,
                text=group["name"],
                fg="#dfe3e9",
                bg="#202329",
                font=("Segoe UI Semibold", 10),
            ).pack(side=tk.LEFT)
            if group.get("batch_launch"):
                tk.Button(
                    section,
                    text="啟動未完成",
                    command=lambda group_id=group["id"]: self.launch_group(group_id),
                    state=tk.DISABLED if self.launching else tk.NORMAL,
                    bg="#292d34",
                    fg="#78a9ff",
                    activebackground="#343943",
                    activeforeground="#9cc0ff",
                    relief=tk.FLAT,
                    bd=0,
                    padx=9,
                    pady=3,
                    font=("Segoe UI Semibold", 8),
                    cursor="hand2",
                ).pack(side=tk.RIGHT)
            tk.Label(
                section,
                text=f"{group_complete}/{len(group_indices)}",
                fg="#858e9b",
                bg="#202329",
                font=("Segoe UI", 9),
            ).pack(side=tk.RIGHT, padx=(0, 9))
            for task_index in group_indices:
                self._render_task_card(task_index)

        if not tasks:
            empty = tk.Frame(self.cards_frame, bg="#292d34")
            empty.pack(fill=tk.BOTH, expand=True, padx=(6, 8), pady=6)
            tk.Label(
                empty,
                text="今天還沒有任務",
                fg="#f4f6fa",
                bg="#292d34",
                font=("Segoe UI Semibold", 15),
            ).pack(pady=(60, 6))
            tk.Label(
                empty,
                text="建立第一個每日啟動項目，之後只要按一次「啟動」。",
                fg="#9199a6",
                bg="#292d34",
                font=("Segoe UI", 10),
                wraplength=320,
            ).pack(pady=(0, 60))

    def _render_task_card(self, task_index: int) -> None:
        task = self.data["tasks"][task_index]
        selected = task_index == self.selected_task_index
        complete = is_task_complete(task)
        if task_index == self.launching_task_index:
            marker, accent, status = "●", "#f2c94c", "正在啟動…"
        elif task_index in self.failed_task_indices:
            marker, accent, status = "●", "#ff7b72", "啟動失敗"
        elif complete:
            marker, accent, status = "●", "#70c58b", self._status_text(task)
        else:
            marker, accent, status = "○", "#7f8a99", self._status_text(task)

        card_bg = "#303641" if selected else "#292d34"
        card = tk.Frame(
            self.cards_frame,
            bg=card_bg,
            highlightthickness=1,
            highlightbackground="#78a9ff" if selected else "#343a44",
            cursor="hand2",
        )
        card.pack(fill=tk.X, padx=(6, 8), pady=5)
        card.grid_columnconfigure(1, weight=1)
        dot = tk.Label(card, text=marker, fg=accent, bg=card_bg,
                       font=("Segoe UI", 16), width=2)
        dot.grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=10)
        title = tk.Label(
            card, text=task["task"], fg="#aeb5c0" if complete else "#f4f6fa",
            bg=card_bg, font=("Segoe UI Semibold", 12), anchor="w",
        )
        title.grid(row=0, column=1, sticky="ew", pady=(10, 0))
        mode = "每日" if task["completion_mode"] == DAILY else "永久"
        meta = tk.Label(
            card,
            text=f"{len(task['quick_launch'])} 個啟動項  ·  {mode}  ·  {status}",
            fg="#9199a6", bg=card_bg, font=("Segoe UI", 9), anchor="w",
        )
        meta.grid(row=1, column=1, sticky="ew", pady=(1, 10))
        tk.Button(
            card,
            text="啟動中" if task_index == self.launching_task_index else ("再開" if complete else "啟動"),
            command=lambda: self.launch_task(task_index),
            state=tk.DISABLED if self.launching else tk.NORMAL,
            bg="#3b76d8" if not complete else "#3a404a", fg="#ffffff",
            activebackground="#4b86e8", activeforeground="#ffffff",
            disabledforeground="#89909c", relief=tk.FLAT, bd=0,
            padx=14, pady=7, font=("Segoe UI Semibold", 9), cursor="hand2",
        ).grid(row=0, column=2, rowspan=2, padx=(8, 4), pady=14)
        tk.Button(
            card, text="⋯", command=lambda: self._show_task_menu(task_index),
            bg=card_bg, fg="#aeb5c0", activebackground="#3a404a",
            activeforeground="#ffffff", relief=tk.FLAT, bd=0, width=3,
            font=("Segoe UI Semibold", 12), cursor="hand2",
        ).grid(row=0, column=3, rowspan=2, padx=(0, 5), pady=14)
        for widget in (card, dot, title, meta):
            widget.bind("<Button-1>", lambda _event: self._select_task(task_index))
            widget.bind("<Double-Button-1>", lambda _event: self.launch_task(task_index))

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

    def _show_task_menu(self, index: int) -> None:
        self.selected_task_index = index
        task = self.data["tasks"][index]
        menu = tk.Menu(self.root, tearoff=False)
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

    def edit_task(self, index: int | None = None) -> None:
        if index is None:
            index = self._selected_index()
        if index is None:
            messagebox.showwarning("提醒", "請選擇一個待辦事項", parent=self.root)
            return
        self._open_task_editor(index)

    def _open_task_editor(self, index: int | None = None) -> None:
        is_new = index is None
        if is_new:
            working_task = {
                "task": "",
                "group_id": UNGROUPED_ID,
                "completion_mode": DAILY,
                "last_completed_at": None,
                "quick_launch": [],
            }
        else:
            working_task = copy.deepcopy(self.data["tasks"][index])

        dialog = tk.Toplevel(self.root)
        dialog.title("新增任務" if is_new else "編輯任務")
        dialog.geometry(self._dialog_geometry(620, 580))
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#202329")

        header = tk.Frame(dialog, bg="#17191d", height=64)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="新增任務" if is_new else "編輯任務",
            fg="#f4f6fa",
            bg="#17191d",
            font=("Segoe UI Semibold", 17),
        ).pack(side=tk.LEFT, padx=20, fill=tk.Y)

        content = tk.Frame(dialog, bg="#202329")
        content.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)

        tk.Label(
            content,
            text="任務名稱",
            fg="#aeb5c0",
            bg="#202329",
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, sticky="w")
        name_var = tk.StringVar(value=working_task["task"])
        name_entry = tk.Entry(
            content,
            textvariable=name_var,
            bg="#17191d",
            fg="#f4f6fa",
            insertbackground="#ffffff",
            selectbackground="#3b76d8",
            relief=tk.FLAT,
            bd=0,
            font=("Segoe UI", 11),
        )
        name_entry.grid(row=1, column=0, sticky="ew", ipady=9, pady=(5, 14))

        tk.Label(
            content,
            text="完成模式",
            fg="#aeb5c0",
            bg="#202329",
            font=("Segoe UI Semibold", 9),
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
            fg="#aeb5c0",
            bg="#202329",
            font=("Segoe UI Semibold", 9),
        ).grid(row=2, column=0, columnspan=2, sticky="w")
        group_names = ["未分類", *(group["name"] for group in self.data["groups"])]
        group_name_to_id = {
            "未分類": UNGROUPED_ID,
            **{group["name"]: group["id"] for group in self.data["groups"]},
        }
        current_group = next(
            (
                group["name"]
                for group in self.data["groups"]
                if group["id"] == working_task.get("group_id", UNGROUPED_ID)
            ),
            "未分類",
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

        section_row = tk.Frame(content, bg="#202329")
        section_row.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(2, 6))
        tk.Label(
            section_row,
            text="啟動項目",
            fg="#f4f6fa",
            bg="#202329",
            font=("Segoe UI Semibold", 11),
        ).pack(side=tk.LEFT)
        launch_count_label = tk.Label(
            section_row,
            fg="#78a9ff",
            bg="#202329",
            font=("Segoe UI", 9),
        )
        launch_count_label.pack(side=tk.RIGHT)

        launch_list = tk.Listbox(
            content,
            bg="#17191d",
            fg="#d6dae1",
            selectbackground="#344f78",
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#343a44",
            bd=0,
            font=("Segoe UI", 9),
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
                launch_list.insert(tk.END, f"  {target.name}{detail}    —    {target.parent}")
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
                    {"target": item["target"], "args": value, "cwd": item.get("cwd")}
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

        launch_controls = tk.Frame(content, bg="#202329")
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
            text="移除",
            command=remove_launcher,
            style="Secondary.TButton",
        ).pack(side=tk.LEFT)

        footer = tk.Frame(dialog, bg="#17191d", height=62)
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
            bg="#3b76d8",
            fg="white",
            activebackground="#4b86e8",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=20,
            pady=8,
            font=("Segoe UI Semibold", 10),
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 20), pady=13)
        tk.Button(
            footer,
            text="取消",
            command=dialog.destroy,
            bg="#292d34",
            fg="#d6dae1",
            activebackground="#343943",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=8,
            font=("Segoe UI", 10),
            cursor="hand2",
        ).pack(side=tk.RIGHT, pady=13)

        refresh_launches()
        name_entry.focus_set()
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("設定")
        dialog.geometry(self._dialog_geometry(600, 540))
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg="#202329")

        header = tk.Frame(dialog, bg="#17191d", height=58)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(
            header,
            text="偏好設定",
            fg="#f4f6fa",
            bg="#17191d",
            font=("Segoe UI Semibold", 16),
        ).pack(side=tk.LEFT, padx=18, fill=tk.Y)

        auto_hide = tk.BooleanVar(
            value=self.data["settings"].get("auto_hide_after_launch", True)
        )
        working_groups = copy.deepcopy(self.data["groups"])
        setting_area = tk.Frame(dialog, bg="#202329")
        setting_area.pack(fill=tk.BOTH, expand=True, padx=20, pady=14)
        ttk.Checkbutton(
            setting_area,
            text="成功啟動後自動收合面板",
            variable=auto_hide,
        ).pack(anchor="w")
        tk.Label(
            setting_area,
            text="關閉後，啟動完成時面板會維持展開。",
            fg="#8f98a6",
            bg="#202329",
            font=("Segoe UI", 9),
        ).pack(anchor="w", padx=(24, 0), pady=(4, 0))

        tk.Frame(setting_area, bg="#343a44", height=1).pack(
            fill=tk.X, pady=(14, 12)
        )
        group_heading = tk.Frame(setting_area, bg="#202329")
        group_heading.pack(fill=tk.X)
        tk.Label(
            group_heading,
            text="任務群組",
            fg="#f4f6fa",
            bg="#202329",
            font=("Segoe UI Semibold", 11),
        ).pack(side=tk.LEFT)
        tk.Label(
            group_heading,
            text="顯示順序由上到下",
            fg="#858e9b",
            bg="#202329",
            font=("Segoe UI", 9),
        ).pack(side=tk.RIGHT)

        group_area = tk.Frame(setting_area, bg="#202329")
        group_area.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        group_list = tk.Listbox(
            group_area,
            width=24,
            bg="#17191d",
            fg="#dfe3e9",
            selectbackground="#344f78",
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#343a44",
            bd=0,
            font=("Segoe UI", 10),
            activestyle="none",
        )
        group_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        editor = tk.Frame(group_area, bg="#292d34", width=280)
        editor.pack(side=tk.LEFT, fill=tk.BOTH, padx=(12, 0))
        editor.pack_propagate(False)
        tk.Label(
            editor, text="群組名稱", fg="#aeb5c0", bg="#292d34",
            font=("Segoe UI Semibold", 9),
        ).pack(anchor="w", padx=14, pady=(14, 5))
        group_name = tk.StringVar()
        group_entry = tk.Entry(
            editor, textvariable=group_name, bg="#17191d", fg="#f4f6fa",
            insertbackground="#ffffff", selectbackground="#3b76d8",
            relief=tk.FLAT, bd=0, font=("Segoe UI", 11),
        )
        group_entry.pack(fill=tk.X, padx=14, ipady=8)
        group_batch = tk.BooleanVar()
        ttk.Checkbutton(
            editor,
            text="顯示「啟動未完成」按鈕",
            variable=group_batch,
            style="Group.TCheckbutton",
        ).pack(anchor="w", padx=10, pady=(12, 0))
        tk.Label(
            editor,
            text="適合背景程式；一次啟動群組內尚未完成的任務。",
            fg="#8f98a6", bg="#292d34", font=("Segoe UI", 9),
            wraplength=235, justify=tk.LEFT,
        ).pack(anchor="w", padx=14, pady=(5, 0))

        editor_index: int | None = None

        def refresh_groups(selected: int | None = None) -> None:
            group_list.delete(0, tk.END)
            for group in working_groups:
                suffix = "   ·   可整組啟動" if group["batch_launch"] else ""
                group_list.insert(tk.END, f"  {group['name']}{suffix}")
            if selected is not None and working_groups:
                selected = max(0, min(selected, len(working_groups) - 1))
                group_list.selection_set(selected)
                group_list.activate(selected)

        def load_group(index: int) -> None:
            nonlocal editor_index
            editor_index = index
            group_name.set(working_groups[index]["name"])
            group_batch.set(working_groups[index]["batch_launch"])

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
            working_groups[editor_index]["batch_launch"] = group_batch.get()
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
                {"id": uuid.uuid4().hex, "name": name, "batch_launch": False}
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
        group_buttons = tk.Frame(setting_area, bg="#202329")
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
            previous_auto_hide = self.data["settings"].get(
                "auto_hide_after_launch", True
            )
            previous_task_groups = [task.get("group_id", UNGROUPED_ID) for task in self.data["tasks"]]
            self.data["settings"]["auto_hide_after_launch"] = auto_hide.get()
            self.data["groups"] = working_groups
            for task in self.data["tasks"]:
                if task.get("group_id") not in valid_group_ids:
                    task["group_id"] = UNGROUPED_ID
            if self._save():
                self.update_listbox()
                self.show_toast("設定已儲存")
                dialog.destroy()
            else:
                self.data["groups"] = previous_groups
                self.data["settings"]["auto_hide_after_launch"] = previous_auto_hide
                for task, group_id in zip(self.data["tasks"], previous_task_groups):
                    task["group_id"] = group_id

        controls = tk.Frame(dialog, bg="#17191d", height=58)
        controls.pack(fill=tk.X)
        controls.pack_propagate(False)
        tk.Button(
            controls,
            text="儲存設定",
            command=save_settings,
            bg="#3b76d8",
            fg="white",
            activebackground="#4b86e8",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=18,
            pady=7,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        ).pack(side=tk.RIGHT, padx=(8, 16), pady=12)
        tk.Button(
            controls,
            text="取消",
            command=dialog.destroy,
            bg="#292d34",
            fg="#d6dae1",
            activebackground="#343943",
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            padx=16,
            pady=7,
            font=("Segoe UI", 9),
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
            if task.get("group_id", UNGROUPED_ID) == group_id
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
        toast.configure(bg="#202020")
        tk.Label(
            toast,
            text=message,
            bg="#202020",
            fg="#ffffff",
            padx=16,
            pady=10,
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
