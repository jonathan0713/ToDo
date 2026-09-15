from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path
from typing import Any

from task_store import normalize_launch_item


class LaunchError(RuntimeError):
    pass


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
