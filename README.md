# TodoLauncher

TodoLauncher 是一個 Windows 側邊待辦與快捷啟動面板。每個待辦可以綁定多個執行檔、捷徑或 URL 檔，從側邊箭頭或系統匣快速叫出。

當一個待辦的所有啟動項目都成功送出後，程式會自動將它標記完成。任務預設採每日模式，隔天會自然回到未完成；也可以在編輯任務時改成永久完成模式。

清單會將尚未完成的任務排在前面，並顯示最後完成時間。啟動成功時使用短暫通知提示，預設會在成功後自動收合；此行為可在設定中關閉。標題列的 `×` 只會收合面板，真正退出請使用系統匣或側邊箭頭的右鍵選單。

主畫面中的每張任務卡都能直接啟動；較少使用的編輯、手動完成與刪除操作集中在 `⋯` 選單。新增或編輯任務時，可以在同一個視窗設定完成模式並一次選取多個啟動檔案。

任務可以依工作情境分組。設定中可新增、改名、刪除與調整群組順序，也能為適合背景執行的群組開啟「啟動未完成」按鈕；刪除群組時，原任務會保留並移到「未分類」。舊版資料第一次載入時也會先維持未分類，不會自動猜測分組。

滑鼠位於主面板的任意位置時都可以用滾輪瀏覽任務，不必刻意移到捲軸或清單空白處。

## 開發環境

需要 Python 3.10 以上版本。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python todo.py
```

程式首次啟動時，會將專案目錄內既有的 `tasks.json` 複製到 `%LOCALAPPDATA%\TodoLauncher\tasks.json`。之後只會讀寫使用者資料目錄，不會修改 repo 內的資料檔。

## 操作

- `Insert`：新增待辦
- `Space`：切換完成狀態
- `Enter` 或雙擊：啟動選取項目的所有程式
- `Delete`：刪除選取項目（會再次確認）
- 側邊箭頭：展開或收合面板
- 系統匣圖示：顯示面板或退出程式

## 測試

```powershell
python -m unittest discover -s tests -v
```

## 封裝

```powershell
python -m pip install -r requirements-dev.txt
python -m PyInstaller --clean todo.spec
```

輸出位於 `dist\todo.exe`。系統匣與主視窗已整合在同一程序中，不需要另外安裝 Python，也不需要啟動 `tray_app.exe`。

建議使用全新的虛擬環境封裝，避免 Conda base 環境中不相關的套件被一併收進執行檔。若 PyInstaller 顯示 Tkinter 被排除，請先確認該 Python 環境能正常執行 `python -m tkinter`。
