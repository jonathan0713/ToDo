import json
import tempfile
import unittest
from pathlib import Path

from datetime import datetime, timedelta, timezone

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


class NormalizeLaunchItemTests(unittest.TestCase):
    def test_migrates_legacy_executable_and_arguments(self) -> None:
        item = normalize_launch_item(
            r"C:\ProgramData\Example\Starter.exe protocol:games/123"
        )
        self.assertEqual(item["target"], r"C:\ProgramData\Example\Starter.exe")
        self.assertEqual(item["args"], ["protocol:games/123"])


class CompletionTests(unittest.TestCase):
    def test_daily_task_only_counts_completion_today(self) -> None:
        now = datetime(2026, 9, 15, 12, tzinfo=timezone.utc)
        task = {"completion_mode": DAILY, "last_completed_at": None}
        set_task_complete(task, True, now)
        self.assertTrue(is_task_complete(task, now))
        self.assertFalse(is_task_complete(task, now + timedelta(days=1)))

    def test_permanent_task_stays_complete(self) -> None:
        completed = datetime(2020, 1, 1, tzinfo=timezone.utc).isoformat()
        task = {"completion_mode": PERMANENT, "last_completed_at": completed}
        self.assertTrue(
            is_task_complete(task, datetime(2026, 9, 15, tzinfo=timezone.utc))
        )


class TaskStoreTests(unittest.TestCase):
    def test_migrates_and_normalizes_legacy_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legacy = root / "legacy.json"
            destination = root / "data" / "tasks.json"
            legacy.write_text(
                json.dumps(
                    {
                        "tasks": [
                            {
                                "task": " Example ",
                                "done": 1,
                                "quick_launch": ["C:/Tools/tool.exe --quiet"],
                            }
                        ],
                        "quick_launch": "unused legacy value",
                    }
                ),
                encoding="utf-8",
            )

            data = TaskStore(destination, legacy).load()

            self.assertTrue(destination.exists())
            self.assertEqual(data["version"], 6)
            self.assertTrue(data["settings"]["auto_hide_after_launch"])
            self.assertEqual(data["settings"]["density"], "comfortable")
            self.assertEqual(data["settings"]["panel_width"], 460)
            self.assertEqual(len(data["groups"]), 3)
            self.assertEqual(data["tasks"][0]["task"], "Example")
            self.assertEqual(data["tasks"][0]["group_id"], UNGROUPED_ID)
            self.assertEqual(data["tasks"][0]["completion_mode"], DAILY)
            self.assertIsNotNone(data["tasks"][0]["last_completed_at"])
            self.assertEqual(
                data["tasks"][0]["quick_launch"][0],
                {"target": "C:/Tools/tool.exe", "args": ["--quiet"]},
            )

    def test_save_replaces_file_with_normalized_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            store = TaskStore(path)
            store.save(
                {
                    "tasks": [
                        {"task": "Task", "done": False, "quick_launch": ["C:/app.exe"]}
                    ]
                }
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["version"], 6)
            self.assertTrue(saved["settings"]["auto_hide_after_launch"])
            self.assertEqual(
                saved["tasks"][0]["quick_launch"][0],
                {"target": "C:/app.exe", "args": []},
            )
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_custom_groups_are_preserved_and_invalid_group_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            store = TaskStore(path)
            store.save(
                {
                    "groups": [
                        {"id": "morning", "name": "早晨流程", "batch_launch": True}
                    ],
                    "tasks": [
                        {"task": "Valid", "group_id": "morning"},
                        {"task": "Missing", "group_id": "deleted"},
                    ],
                }
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["groups"][0]["name"], "早晨流程")
            self.assertTrue(saved["groups"][0]["batch_launch"])
            self.assertEqual(saved["groups"][0]["icon"], "●")
            self.assertEqual(saved["groups"][0]["color"], "#78a9ff")
            self.assertEqual(saved["tasks"][0]["group_id"], "morning")
            self.assertEqual(saved["tasks"][1]["group_id"], UNGROUPED_ID)

    def test_appearance_settings_and_group_identity_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            store = TaskStore(path)
            store.save(
                {
                    "settings": {
                        "density": "compact",
                        "panel_width": 999,
                        "show_task_details": False,
                    },
                    "groups": [
                        {
                            "id": "games",
                            "name": "遊戲",
                            "icon": "🎮",
                            "color": "#ef7f91",
                            "collapsed": True,
                        }
                    ],
                    "tasks": [],
                }
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["settings"]["density"], "compact")
            self.assertEqual(saved["settings"]["panel_width"], 620)
            self.assertFalse(saved["settings"]["show_task_details"])
            self.assertEqual(saved["groups"][0]["icon"], "🎮")
            self.assertEqual(saved["groups"][0]["color"], "#ef7f91")
            self.assertTrue(saved["groups"][0]["collapsed"])

    def test_version_four_default_groups_gain_distinct_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 4,
                        "groups": [
                            {
                                "id": "light_manual",
                                "name": "輕量手操",
                                "batch_launch": False,
                            }
                        ],
                        "tasks": [],
                    }
                ),
                encoding="utf-8",
            )

            loaded = TaskStore(path).load()

            self.assertEqual(loaded["groups"][0]["icon"], "☀")
            self.assertEqual(loaded["groups"][0]["color"], "#70c58b")

    def test_auto_group_tasks_and_group_rules_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            store = TaskStore(path)
            store.save(
                {
                    "groups": [
                        {
                            "id": "next",
                            "name": "下一步",
                            "rule": "unfinished",
                        }
                    ],
                    "tasks": [
                        {"task": "Automatic", "group_id": AUTO_GROUP_ID}
                    ],
                }
            )

            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["groups"][0]["rule"], "unfinished")
            self.assertEqual(saved["tasks"][0]["group_id"], AUTO_GROUP_ID)

    def test_corrupt_file_is_backed_up(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            path.write_text("{broken", encoding="utf-8")
            store = TaskStore(path)

            data = store.load()

            self.assertEqual(data["tasks"], [])
            self.assertIsNotNone(store.last_warning)
            self.assertEqual(len(list(path.parent.glob("tasks.json.corrupt-*.bak"))), 1)


if __name__ == "__main__":
    unittest.main()
