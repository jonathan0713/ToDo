import unittest
from unittest.mock import Mock, patch

from launcher import LaunchError
from task_store import AUTO_GROUP_ID, DAILY, is_task_complete
from todo import TodoApp


class AutoCompletionTests(unittest.TestCase):
    @staticmethod
    def make_app() -> TodoApp:
        app = object.__new__(TodoApp)
        app.root = None
        app.launching = False
        app.launching_task_index = None
        app.failed_task_indices = set()
        app.data = {
            "settings": {"auto_hide_after_launch": False},
            "tasks": [
                {
                    "task": "Daily task",
                    "completion_mode": DAILY,
                    "last_completed_at": None,
                    "quick_launch": [
                        {"target": "C:/one.exe", "args": []},
                        {"target": "C:/two.exe", "args": []},
                    ],
                }
            ]
        }
        app._selected_index = Mock(return_value=0)
        app._save = Mock(return_value=True)
        app.update_listbox = Mock()
        app.show_toast = Mock()
        app.root = Mock()
        return app

    @patch("todo.launch")
    def test_all_launches_succeed_marks_task_complete(self, launch_mock: Mock) -> None:
        app = self.make_app()

        app.launch_selected()

        self.assertEqual(launch_mock.call_count, 2)
        self.assertTrue(is_task_complete(app.data["tasks"][0]))
        app._save.assert_called_once_with()
        self.assertEqual(app.update_listbox.call_count, 2)
        app.update_listbox.assert_called_with(0)
        app.show_toast.assert_called_once_with("已啟動 2 個項目，今日完成")

    @patch("todo.messagebox.showerror")
    @patch("todo.launch", side_effect=[None, LaunchError("second failed")])
    def test_partial_failure_does_not_complete_task(
        self, launch_mock: Mock, show_error: Mock
    ) -> None:
        app = self.make_app()

        app.launch_selected()

        self.assertEqual(launch_mock.call_count, 2)
        self.assertFalse(is_task_complete(app.data["tasks"][0]))
        app._save.assert_not_called()
        show_error.assert_called_once()

    @patch("todo.launch")
    def test_group_launch_completes_each_unfinished_task(self, launch_mock: Mock) -> None:
        app = self.make_app()
        app.data["tasks"].append(
            {
                "task": "Second task",
                "group_id": "background",
                "completion_mode": DAILY,
                "last_completed_at": None,
                "quick_launch": [{"target": "C:/three.exe", "args": []}],
            }
        )
        app.data["tasks"][0]["group_id"] = "background"

        app.launch_group("background")

        self.assertEqual(launch_mock.call_count, 3)
        self.assertTrue(all(is_task_complete(task) for task in app.data["tasks"]))
        app._save.assert_called_once_with()
        app.show_toast.assert_called_once_with("已啟動 2 個任務，並標記完成")


class DialogPlacementTests(unittest.TestCase):
    def test_wide_dialog_is_kept_inside_work_area(self) -> None:
        app = object.__new__(TodoApp)
        app.root = Mock()
        app.root.winfo_x.return_value = 1460
        app.root.winfo_y.return_value = 360
        app.window_width = 460
        app.window_height = 666
        app.work_left = 0
        app.work_top = 0
        app.work_right = 1920
        app.work_bottom = 1026

        geometry = app._dialog_geometry(620, 480)

        self.assertEqual(geometry, "620x480+1300+453")


class WheelInteractionTests(unittest.TestCase):
    def test_wheel_scrolls_cards_when_panel_is_visible(self) -> None:
        app = object.__new__(TodoApp)
        app.visible = True
        app.cards_canvas = Mock()
        event = Mock(delta=-120)

        app._on_mousewheel(event)

        app.cards_canvas.yview_scroll.assert_called_once_with(3, "units")

    def test_wheel_does_not_scroll_hidden_panel(self) -> None:
        app = object.__new__(TodoApp)
        app.visible = False
        app.cards_canvas = Mock()

        app._on_mousewheel(Mock(delta=-120))

        app.cards_canvas.yview_scroll.assert_not_called()


class TaskReorderingTests(unittest.TestCase):
    @staticmethod
    def make_app() -> TodoApp:
        app = object.__new__(TodoApp)
        app.data = {
            "groups": [
                {"id": "a", "rule": "manual"},
                {"id": "b", "rule": "manual"},
            ],
            "tasks": [
                {"task": "One", "group_id": "a"},
                {"task": "Two", "group_id": "a"},
                {"task": "Three", "group_id": "b"},
            ]
        }
        app._save = Mock(return_value=True)
        app.update_listbox = Mock()
        app.show_toast = Mock()
        return app

    def test_drop_after_task_reorders_within_group(self) -> None:
        app = self.make_app()

        app._move_task_to_drop_target(0, ("task", 1, "after"))

        self.assertEqual(
            [task["task"] for task in app.data["tasks"]],
            ["Two", "One", "Three"],
        )

    def test_drop_on_task_moves_to_target_group(self) -> None:
        app = self.make_app()

        app._move_task_to_drop_target(0, ("task", 2, "before"))

        self.assertEqual(app.data["tasks"][1]["task"], "One")
        self.assertEqual(app.data["tasks"][1]["group_id"], "b")


class SmartGroupTests(unittest.TestCase):
    def test_auto_task_uses_first_matching_rule_without_duplication(self) -> None:
        app = object.__new__(TodoApp)
        app.data = {
            "groups": [
                {"id": "next", "rule": "unfinished"},
                {"id": "daily", "rule": "daily"},
            ]
        }
        task = {
            "group_id": AUTO_GROUP_ID,
            "completion_mode": DAILY,
            "last_completed_at": None,
        }

        self.assertEqual(app._effective_group_id(task), "next")

    def test_manual_group_overrides_smart_rules(self) -> None:
        app = object.__new__(TodoApp)
        app.data = {"groups": [{"id": "next", "rule": "unfinished"}]}
        task = {
            "group_id": "games",
            "completion_mode": DAILY,
            "last_completed_at": None,
        }

        self.assertEqual(app._effective_group_id(task), "games")

    def test_reordering_auto_peers_keeps_automatic_membership(self) -> None:
        app = object.__new__(TodoApp)
        app.data = {
            "groups": [{"id": "next", "rule": "unfinished"}],
            "tasks": [
                {
                    "task": "One",
                    "group_id": AUTO_GROUP_ID,
                    "last_completed_at": None,
                },
                {
                    "task": "Two",
                    "group_id": AUTO_GROUP_ID,
                    "last_completed_at": None,
                },
            ],
        }
        app._save = Mock(return_value=True)
        app.update_listbox = Mock()
        app.show_toast = Mock()

        app._move_task_to_drop_target(0, ("task", 1, "after"))

        self.assertTrue(
            all(task["group_id"] == AUTO_GROUP_ID for task in app.data["tasks"])
        )


if __name__ == "__main__":
    unittest.main()
