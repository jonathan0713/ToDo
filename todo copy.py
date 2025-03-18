import tkinter as tk
from tkinter import ttk  # 新增
from tkinter import messagebox, simpledialog
import json
import os
import subprocess
import ctypes  # 新增

# 儲存檔案
TASKS_FILE = "C:/Users/Jonat/Desktop/ToDo/tasks.json"

# 讀取待辦事項
def load_tasks():
    if os.path.exists(TASKS_FILE):
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"tasks": [], "quick_launch": []}

# 儲存待辦事項
def save_tasks():
    with open(TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# 新增待辦
def add_task():
    task = simpledialog.askstring("新增待辦", "請輸入待辦事項：")
    if task:
        data["tasks"].append({"task": task, "done": False, "quick_launch": []})
        save_tasks()
        update_listbox()

# 刪除待辦
def delete_task():
    selected = listbox.curselection()
    if selected:
        del data["tasks"][selected[0]]
        save_tasks()
        update_listbox()

# 標記完成
def toggle_done():
    selected = listbox.curselection()
    if selected:
        idx = selected[0]
        data["tasks"][idx]["done"] = not data["tasks"][idx]["done"]
        save_tasks()
        update_listbox()

# 設定應用程式
def set_quick_launch():
    selected = listbox.curselection()
    if selected:
        path = simpledialog.askstring("設定應用程式", "請輸入應用程式完整路徑：")
        if path and os.path.exists(path):
            data["tasks"][selected[0]]["quick_launch"].append(path)
            save_tasks()
            update_listbox()
        else:
            messagebox.showerror("錯誤", "路徑無效")
    else:
        messagebox.showwarning("提醒", "請選擇一個待辦事項")

# 啟動應用程式
def launch_app():
    selected = listbox.curselection()
    if selected:
        quick_launch_paths = data["tasks"][selected[0]]["quick_launch"]
        if quick_launch_paths:
            for path in quick_launch_paths:
                subprocess.Popen(path, shell=True)
        else:
            messagebox.showwarning("提醒", "未設定應用程式")
    else:
        messagebox.showwarning("提醒", "請選擇一個待辦事項")

# 更新 UI
def update_listbox():
    listbox.delete(0, tk.END)
    for task in data["tasks"]:
        text = f"[✔] {task['task']}" if task["done"] else f"[ ] {task['task']}"
        listbox.insert(tk.END, text)
    quick_launch_label.config(text="應用程式快捷：多個")

# 讀取資料
data = load_tasks()

# 建立 GUI
root = tk.Tk()
root.title("待辦清單")

# 設置暗色調主題
style = ttk.Style()
style.theme_use('clam')
style.configure('TButton', background='#444444', foreground='#ffffff')
style.configure('TLabel', background='#2e2e2e', foreground='#ffffff')
style.configure('TListbox', background='#3e3e3e', foreground='#ffffff')

# 獲取屏幕寬度和高度
screen_width = root.winfo_screenwidth()
screen_height = root.winfo_screenheight()

# 計算窗口的寬度和高度
window_width = int(screen_width * 0.1)  # 窗口寬度為屏幕寬度的 10%
window_height = int(screen_height * 0.93)  # 窗口高度為屏幕高度的 93%

# 計算窗口的位置
x_position = screen_width - window_width  # 窗口固定在屏幕右邊
y_position = 0  # 窗口從屏幕頂部開始

# 設置窗口大小和位置
root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")

# 設定暗色調主題
root.configure(bg="#2e2e2e")

# 設置視窗標題欄顏色
hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int(2)))
ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int(1)))

# 隱藏窗口
root.withdraw()

# 顯示或隱藏窗口的函數
def toggle_window():
    if root.state() == 'withdrawn':
        root.deiconify()
        arrow_window.geometry(f"30x30+{x_position-30}+{int(window_height/2)}")
        arrow_button.config(image=right_arrow_img)
    else:
        root.withdraw()
        arrow_window.geometry(f"30x30+{screen_width-30}+{int(screen_height/2)}")
        arrow_button.config(image=left_arrow_img)

# 創建箭頭按鈕窗口
arrow_window = tk.Toplevel()
arrow_window.overrideredirect(True)
arrow_window.geometry(f"30x30+{x_position-30}+{int(window_height/2)}")
# arrow_window.configure(bg="#2e2e2e")
arrow_window.attributes("-topmost", True)  # 設置窗口總是在最上層

# 加載自定義箭頭圖案
left_arrow_img = tk.PhotoImage(file="C:/Users/Jonat/Desktop/ToDo/left_arrow.png")  # 替換為您的左箭頭圖案文件
right_arrow_img = tk.PhotoImage(file="C:/Users/Jonat/Desktop/ToDo/right_arrow.png")  # 替換為您的右箭頭圖案文件

# 創建箭頭按鈕
arrow_button = tk.Button(arrow_window, image=left_arrow_img, command=toggle_window, bg="#2e2e2e", relief=tk.FLAT)
arrow_button.pack(fill=tk.BOTH, expand=True)

# 調整箭頭按鈕的透明度
arrow_window.attributes("-alpha", 0.5)  # 設置箭頭按鈕窗口透明度
# arrow_button.attributes("-alpha", 1.0)  # 設置箭頭按鈕透明度


# 待辦清單 Listbox
listbox = tk.Listbox(root, width=25, height=45, bg="#3e3e3e", fg="#ffffff")
listbox.pack(pady=10)

# 操作按鈕（垂直排列）
btn_frame = tk.Frame(root, bg="#2e2e2e")
btn_frame.pack()

ttk.Button(btn_frame, text="➕ 新增待辦", command=add_task, width=20).pack(pady=3)
ttk.Button(btn_frame, text="❌ 刪除選擇", command=delete_task, width=20).pack(pady=3)
ttk.Button(btn_frame, text="✔️ 完成/未完成", command=toggle_done, width=20).pack(pady=3)
ttk.Button(btn_frame, text="🚀 設定快捷啟動", command=set_quick_launch, width=20).pack(pady=3)
ttk.Button(btn_frame, text="▶️ 啟動應用程式", command=launch_app, width=20).pack(pady=3)

# 快捷啟動顯示
quick_launch_label = ttk.Label(root, text="應用程式快捷：多個")
quick_launch_label.pack(pady=10)

# left_arrow_img = None
# right_arrow_img = None


# 更新 UI
update_listbox()

# 啟動 GUI
root.mainloop()