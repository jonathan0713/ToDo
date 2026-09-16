from __future__ import annotations

import ctypes
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from task_store import normalize_launch_item


class LaunchError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunningApplication:
    name: str
    title: str
    path: str


def _application_name(window_title: str, executable: Path) -> str:
    """Choose a stable, human-readable name from a top-level window."""
    for separator in (" - ", " — ", " – "):
        if separator in window_title:
            candidate = window_title.rsplit(separator, 1)[-1].strip()
            if candidate and len(candidate) <= 60:
                return candidate
    title = window_title.strip()
    if title and len(title) <= 60:
        return title
    return executable.stem.replace("_", " ").replace("-", " ").strip()


def list_running_applications() -> list[RunningApplication]:
    """Return visible Windows applications, deduplicated by executable path."""
    if os.name != "nt":
        return []

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    process_query_limited_information = 0x1000
    current_process_id = os.getpid()
    applications: dict[str, RunningApplication] = {}
    enum_callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    @enum_callback
    def inspect_window(window: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(window):
            return True
        title_length = user32.GetWindowTextLengthW(window)
        if title_length == 0:
            return True

        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        if process_id.value == current_process_id:
            return True
        process = kernel32.OpenProcess(
            process_query_limited_information, False, process_id.value
        )
        if not process:
            return True
        try:
            size = wintypes.DWORD(32768)
            path_buffer = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(
                process, 0, path_buffer, ctypes.byref(size)
            ):
                return True
        finally:
            kernel32.CloseHandle(process)

        title_buffer = ctypes.create_unicode_buffer(title_length + 1)
        if not user32.GetWindowTextW(window, title_buffer, title_length + 1):
            return True
        title = title_buffer.value.strip()
        executable = Path(path_buffer.value)
        if not title or executable.suffix.lower() not in {".exe", ".com"}:
            return True
        key = os.path.normcase(str(executable))
        applications.setdefault(
            key,
            RunningApplication(
                name=_application_name(title, executable),
                title=title,
                path=str(executable),
            ),
        )
        return True

    user32.EnumWindows(inspect_window, 0)
    return sorted(
        applications.values(),
        key=lambda application: (application.name.casefold(), application.title.casefold()),
    )


def _focus_existing_window(target: Path) -> bool:
    """Focus a visible window owned by target without starting another process."""
    if os.name != "nt":
        return False

    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    process_query_limited_information = 0x1000
    target_path = os.path.normcase(str(target.resolve()))
    matches: list[int] = []

    enum_callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )

    @enum_callback
    def inspect_window(window: int, _parameter: int) -> bool:
        if not user32.IsWindowVisible(window) or user32.GetWindowTextLengthW(window) == 0:
            return True
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
        process = kernel32.OpenProcess(
            process_query_limited_information, False, process_id.value
        )
        if not process:
            return True
        try:
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(size)
            ) and os.path.normcase(buffer.value) == target_path:
                matches.append(window)
                return False
        finally:
            kernel32.CloseHandle(process)
        return True

    user32.EnumWindows(inspect_window, 0)
    if not matches:
        return False
    window = matches[0]
    if user32.IsIconic(window):
        user32.ShowWindow(window, 9)
    user32.SetForegroundWindow(window)
    return True


def launch(raw_item: Any) -> None:
    item = normalize_launch_item(raw_item)
    if item is None:
        raise LaunchError("啟動設定不完整")

    target_text = os.path.expandvars(os.path.expanduser(item["target"]))
    target = Path(target_text)
    if not target.exists():
        raise LaunchError(f"找不到檔案：{target}")

    args = [str(arg) for arg in item.get("args", [])]
    cwd_text = item.get("cwd")
    cwd = Path(os.path.expandvars(os.path.expanduser(cwd_text))) if cwd_text else target.parent
    if not cwd.is_dir():
        raise LaunchError(f"工作目錄不存在：{cwd}")

    suffix = target.suffix.lower()
    try:
        if (
            item.get("focus_existing", False)
            and suffix in {".exe", ".com"}
            and _focus_existing_window(target)
        ):
            return
        if suffix in {".lnk", ".url"}:
            if args:
                raise LaunchError("捷徑與 URL 檔目前不支援額外參數")
            start_file = getattr(os, "startfile", None)
            if start_file is None:
                raise LaunchError("此作業系統不支援開啟捷徑")
            start_file(str(target))
        elif suffix in {".bat", ".cmd"}:
            command_processor = os.environ.get("COMSPEC", "cmd.exe")
            subprocess.Popen(
                [command_processor, "/c", str(target), *args],
                cwd=str(cwd),
                shell=False,
            )
        elif suffix in {".exe", ".com"}:
            subprocess.Popen([str(target), *args], cwd=str(cwd), shell=False)
        else:
            if args:
                raise LaunchError("一般文件目前不支援額外參數")
            start_file = getattr(os, "startfile", None)
            if start_file is None:
                raise LaunchError("此作業系統不支援開啟文件")
            start_file(str(target))
    except LaunchError:
        raise
    except OSError as error:
        raise LaunchError(f"無法啟動 {target.name}：{error}") from error
