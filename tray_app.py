from pystray import Icon, MenuItem, Menu
from PIL import Image
import os
import sys
import subprocess

# 設定圖示檔案路徑
icon_path = "icon.png"
image = Image.open(icon_path)

# 查找系統中的 Python 執行檔（優先使用 Miniconda 的 pythonw.exe）
def find_python_executable():
    # 優先使用 pythonw.exe 以避免控制台視窗
    preferred_path = r"C:\Users\Jonat\miniconda3\pythonw.exe"
    if os.path.exists(preferred_path):
        print(f"使用 Miniconda 的 pythonw: {preferred_path}")
        return preferred_path
    # 如果 pythonw.exe 不可用，fallback 到 python.exe
    fallback_path = r"C:\Users\Jonat\miniconda3\python.exe"
    if os.path.exists(fallback_path):
        try:
            version_result = subprocess.run([fallback_path, "--version"], capture_output=True, text=True)
            if version_result.returncode == 0:
                print(f"使用預設的 Miniconda Python: {fallback_path}")
                return fallback_path
        except Exception:
            pass
    # 如果都找不到，嘗試其他路徑
    try:
        result = subprocess.run("where python", capture_output=True, text=True, shell=True)
        if result.returncode == 0 and result.stdout:
            python_paths = result.stdout.strip().splitlines()
            for path in python_paths:
                if "WindowsApps" not in path:
                    try:
                        version_result = subprocess.run([path, "--version"], capture_output=True, text=True)
                        if version_result.returncode == 0:
                            print(f"找到可用的 Python 執行檔: {path}")
                            return path
                    except Exception:
                        continue
        raise RuntimeError("無法找到有效的 Python 執行檔")
    except Exception as e:
        raise RuntimeError(f"查找 Python 執行檔時發生錯誤: {e}")

# 點擊 "顯示視窗" 時執行的函數
def show_window(icon, item):
    try:
        if getattr(sys, 'frozen', False):
            base_path = os.path.dirname(sys.executable)
            print(f"打包環境，基準路徑: {base_path}")
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
            print(f"未打包環境，基準路徑: {base_path}")

        todo_script = os.path.join(base_path, "todo.py")
        print(f"todo.py 路徑: {todo_script}")

        if not os.path.exists(todo_script):
            raise FileNotFoundError(f"找不到 {todo_script}！請確保它與執行檔在同一目錄下。")

        python_path = find_python_executable()
        cmd = [python_path, todo_script]
        print(f"執行命令: {' '.join(cmd)}")

        # 使用 pythonw.exe 執行，確保無 CMD 視窗
        subprocess.Popen(
            cmd,
            cwd=base_path,
            creationflags=subprocess.CREATE_NO_WINDOW,  # 進一步確保無視窗
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL
        )

    except Exception as e:
        print(f"執行 todo.py 時發生錯誤: {e}")

# 點擊 "退出" 時執行的函數
def exit_app(icon, item):
    icon.stop()

# 設定系統托盤選單
menu = Menu(
    MenuItem("顯示視窗", show_window),
    MenuItem("退出", exit_app)
)

# 創建系統托盤應用
icon = Icon("MyApp", image, menu=menu)

# 運行系統托盤應用
icon.run()