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


if __name__ == "__main__":
    unittest.main()
