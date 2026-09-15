from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from task_store import normalize_launch_item


class LaunchError(RuntimeError):
    pass


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
