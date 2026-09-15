from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 4
DAILY = "daily"
PERMANENT = "permanent"
COMPLETION_MODES = {DAILY, PERMANENT}
_TARGET_PATTERN = re.compile(r"\.(?:exe|com|bat|cmd|lnk|url)\b", re.IGNORECASE)
DEFAULT_SETTINGS = {"auto_hide_after_launch": True}
UNGROUPED_ID = "ungrouped"
DEFAULT_GROUPS = [
    {"id": "background", "name": "背景執行", "batch_launch": True},
    {"id": "light_manual", "name": "輕量手操", "batch_launch": False},
    {"id": "heavy_manual", "name": "重度手操", "batch_launch": False},
]


def empty_data() -> dict[str, Any]:
    return {
        "version": SCHEMA_VERSION,
        "settings": DEFAULT_SETTINGS.copy(),
        "groups": [group.copy() for group in DEFAULT_GROUPS],
        "tasks": [],
    }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _valid_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def is_task_complete(task: dict[str, Any], now: datetime | None = None) -> bool:
    completed_at = _valid_timestamp(task.get("last_completed_at"))
    if completed_at is None:
        return False
    if task.get("completion_mode", DAILY) == PERMANENT:
        return True
    current = now.astimezone() if now else datetime.now().astimezone()
    completed = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    if completed.tzinfo is None:
        completed = completed.astimezone()
    return completed.astimezone().date() == current.date()


def set_task_complete(
    task: dict[str, Any], complete: bool, now: datetime | None = None
) -> None:
    if complete:
        current = now.astimezone() if now else datetime.now().astimezone()
        task["last_completed_at"] = current.isoformat(timespec="seconds")
    else:
        task["last_completed_at"] = None


def _split_arguments(value: str) -> list[str]:
    if not value.strip():
        return []
    try:
        return [part.strip('"') for part in shlex.split(value, posix=False)]
    except ValueError:
        return [value.strip()]


def normalize_launch_item(raw: Any) -> dict[str, Any] | None:
    """Convert legacy launch strings and current dictionaries to one schema."""
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            return None
        match = _TARGET_PATTERN.search(value)
        if match:
            target = value[: match.end()].strip().strip('"')
            args = _split_arguments(value[match.end() :])
        else:
            target, args = value.strip('"'), []
        return {"target": target, "args": args}

    if not isinstance(raw, dict):
        return None

    target = str(raw.get("target", "")).strip()
    if not target:
        return None

    args_value = raw.get("args", [])
    if isinstance(args_value, str):
        args = _split_arguments(args_value)
    elif isinstance(args_value, list):
        args = [str(arg) for arg in args_value]
    else:
        args = []

    item: dict[str, Any] = {"target": target, "args": args}
    cwd = raw.get("cwd")
    if cwd:
        item["cwd"] = str(cwd)
    return item


def normalize_data(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict) or not isinstance(raw.get("tasks", []), list):
        raise ValueError("資料格式必須包含 tasks 陣列")

    raw_settings = raw.get("settings", {})
    if not isinstance(raw_settings, dict):
        raw_settings = {}
    settings = {
        "auto_hide_after_launch": bool(
            raw_settings.get("auto_hide_after_launch", True)
        )
    }

    raw_groups = raw.get("groups", DEFAULT_GROUPS)
    if not isinstance(raw_groups, list):
        raw_groups = DEFAULT_GROUPS
    groups: list[dict[str, Any]] = []
    group_ids: set[str] = set()
    group_names: set[str] = set()
    for raw_group in raw_groups:
        if not isinstance(raw_group, dict):
            continue
        group_id = str(raw_group.get("id", "")).strip()
        name = str(raw_group.get("name", "")).strip()
        normalized_name = name.casefold()
        if (
            not group_id
            or group_id == UNGROUPED_ID
            or group_id in group_ids
            or not name
            or normalized_name in group_names
        ):
            continue
        groups.append(
            {
                "id": group_id,
                "name": name,
                "batch_launch": bool(raw_group.get("batch_launch", False)),
            }
        )
        group_ids.add(group_id)
        group_names.add(normalized_name)

    tasks: list[dict[str, Any]] = []
    for raw_task in raw.get("tasks", []):
        if not isinstance(raw_task, dict):
            continue
        name = str(raw_task.get("task", "")).strip()
        if not name:
            continue
        raw_launches = raw_task.get("quick_launch", [])
        if isinstance(raw_launches, str):
            raw_launches = [raw_launches]
        elif not isinstance(raw_launches, list):
            raw_launches = []
        launches = [
            item
            for item in (
                normalize_launch_item(value)
                for value in raw_launches
            )
            if item is not None
        ]
        completion_mode = raw_task.get("completion_mode", DAILY)
        if completion_mode not in COMPLETION_MODES:
            completion_mode = DAILY
        last_completed_at = _valid_timestamp(raw_task.get("last_completed_at"))
        if last_completed_at is None and raw_task.get("done", False):
            last_completed_at = _now_iso()
        tasks.append(
            {
                "task": name,
                "group_id": (
                    str(raw_task.get("group_id", UNGROUPED_ID)).strip()
                    if str(raw_task.get("group_id", UNGROUPED_ID)).strip() in group_ids
                    else UNGROUPED_ID
                ),
                "completion_mode": completion_mode,
                "last_completed_at": last_completed_at,
                "quick_launch": launches,
            }
        )

    return {
        "version": SCHEMA_VERSION,
        "settings": settings,
        "groups": groups,
        "tasks": tasks,
    }


class TaskStore:
    def __init__(self, path: Path, legacy_path: Path | None = None) -> None:
        self.path = Path(path)
        self.legacy_path = Path(legacy_path) if legacy_path else None
        self.last_warning: str | None = None

    def load(self) -> dict[str, Any]:
        self.last_warning = None
        source = self.path
        try:
            self._migrate_legacy_file()
        except OSError as error:
            self.last_warning = f"無法搬移舊資料：{error}"
            if self.legacy_path and self.legacy_path.exists():
                source = self.legacy_path
        if not source.exists():
            return empty_data()

        try:
            with source.open("r", encoding="utf-8") as file:
                return normalize_data(json.load(file))
        except (OSError, json.JSONDecodeError, ValueError) as error:
            backup = self._backup_corrupt_file(source)
            backup_note = f"，備份位於 {backup}" if backup else ""
            self.last_warning = f"無法讀取待辦資料：{error}{backup_note}"
            return empty_data()

    def save(self, data: dict[str, Any]) -> None:
        normalized = normalize_data(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=f".{self.path.stem}-",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                json.dump(normalized, temporary_file, indent=2, ensure_ascii=False)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
                temporary_path = Path(temporary_file.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path and temporary_path.exists():
                temporary_path.unlink()

    def _migrate_legacy_file(self) -> None:
        if self.path.exists() or not self.legacy_path or not self.legacy_path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.legacy_path, self.path)

    @staticmethod
    def _backup_corrupt_file(source: Path) -> Path | None:
        if not source.exists():
            return None
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = source.with_name(f"{source.name}.corrupt-{stamp}.bak")
        try:
            shutil.copy2(source, backup)
            return backup
        except OSError:
            return None
