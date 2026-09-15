import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launcher import LaunchError, launch


class LauncherTests(unittest.TestCase):
    def test_executable_uses_argument_list_without_shell(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "example.exe"
            executable.touch()

            with patch("launcher.subprocess.Popen") as popen:
                launch({"target": str(executable), "args": ["--quiet", "two words"]})

            popen.assert_called_once_with(
                [str(executable), "--quiet", "two words"],
                cwd=str(executable.parent),
                shell=False,
            )

    def test_missing_target_has_clear_error(self) -> None:
        with self.assertRaisesRegex(LaunchError, "找不到檔案"):
            launch({"target": "Z:/definitely-missing/example.exe", "args": []})

    def test_focuses_existing_executable_without_starting_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "example.exe"
            executable.touch()

            with (
                patch("launcher._focus_existing_window", return_value=True) as focus,
                patch("launcher.subprocess.Popen") as popen,
            ):
                launch({"target": str(executable), "focus_existing": True})

            focus.assert_called_once_with(executable)
            popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
