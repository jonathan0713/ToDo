import unittest
from unittest.mock import Mock, patch

from launcher import LaunchError
from task_store import DAILY, is_task_complete
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


if __name__ == "__main__":
    unittest.main()
