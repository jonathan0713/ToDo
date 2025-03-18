import tkinter as tk
from tkinter import ttk  # 新增
from tkinter import messagebox, simpledialog
from tkinter import font
from tkinter import filedialog  # 新增
import json
import os
import subprocess

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
        path = filedialog.askopenfilename(title="選擇應用程式", filetypes=[("可執行文件", "*.exe"), ("所有文件", "*.*")])
        if path:
            data["tasks"][selected[0]]["quick_launch"].append(path)
            save_tasks()
            update_listbox()
        else:
            messagebox.showerror("錯誤", "未選擇任何文件")
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
    # quick_launch_label.config(text="應用程式快捷：多個")
    listbox.config(font=listbox_font)

def close_app():
    root.destroy()

def start_move(event):
    root.x = event.x
    root.y = event.y

def move_app(event):
    x = root.winfo_x() + (event.x - root.x)
    y = root.winfo_y() + (event.y - root.y)
    root.geometry(f"+{x}+{y}")

def on_double_click(event):
    launch_app()

# 讀取資料
data = load_tasks()

# 建立 GUI
root = tk.Tk()
root.overrideredirect(True)

# 自訂標題欄
title_bar = tk.Frame(root, bg="#202020")
title_bar.place(x=0, y=0, relwidth=1, relheight=0.08)

# 標題文字
title_label = tk.Label(title_bar, text="我的應用程式", fg="white", bg="#202020")
title_label.place(relx=0.05, rely=0.05)

# 關閉按鈕
close_button = tk.Button(root, text="X", command=close_app, fg="white", bg="#202020", bd=0)
close_button.place(relx=0.92, y=0, relwidth=0.08, relheight=0.08)

# 讓標題欄可以拖動視窗
title_bar.bind("<Button-1>", start_move)
title_bar.bind("<B1-Motion>", move_app)

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
window_width = int(screen_width * 0.15)  # 窗口寬度為屏幕寬度的 10%
window_height = int(screen_height * 0.2)  # 窗口高度為屏幕高度的 93%

# 計算窗口的位置
x_position = screen_width - window_width  # 窗口固定在屏幕右邊
y_position = int(0.92 * screen_height - window_height)  # 窗口從屏幕頂部開始

# 設置窗口大小和位置
root.geometry(f"{window_width}x{window_height}+{x_position}+{y_position}")

# 設定暗色調主題
root.configure(bg="#2e2e2e")


# 隱藏窗口
root.withdraw()

# 顯示或隱藏窗口的函數
def toggle_window():
    if root.state() == 'withdrawn':
        root.deiconify()
        arrow_window.geometry(f"30x30+{x_position-30}+{int(0.92 * screen_height - window_height)}")
        arrow_button.config(image=right_arrow_img)
    else:
        root.withdraw()
        arrow_window.geometry(f"30x30+{screen_width-30}+{int(screen_height - window_height/2)}")
        arrow_button.config(image=left_arrow_img)

# 創建箭頭按鈕窗口
arrow_window = tk.Toplevel()
arrow_window.overrideredirect(True)
arrow_window.geometry(f"30x30+{x_position-30}+{int(0.92 * screen_height - window_height)}")
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
listbox_font = font.Font(size=14)  # 你可以根據需要調整字體大小
listbox = tk.Listbox(root, bg="#3e3e3e", fg="#ffffff")
listbox.place(relwidth=0.7, relheight=0.8, relx=0.05, rely=0.1)


listbox.bind("<Double-Button-1>", on_double_click)

# 操作按鈕（垂直排列）
btn_frame = tk.Frame(root, bg="#2e2e2e")
btn_frame.place(relwidth=0.3, relheight=0.8, relx=0.75, rely=0.1)
velue_height = 0.15
velue_width = 0.4

ttk.Button(btn_frame, text="➕", command=add_task, width=20).place(relwidth=velue_width, relheight=velue_height, relx=0.1, rely=0.05)
ttk.Button(btn_frame, text="❌", command=delete_task, width=2).place(relwidth=velue_width, relheight=velue_height, relx=0.1, rely=0.2)
ttk.Button(btn_frame, text="✔️", command=toggle_done, width=2).place(relwidth=velue_width, relheight=velue_height, relx=0.1, rely=0.35)
ttk.Button(btn_frame, text="🚀", command=set_quick_launch, width=2).place(relwidth=velue_width, relheight=velue_height, relx=0.1, rely=0.5)
ttk.Button(btn_frame, text="▶️", command=launch_app, width=2).place(relwidth=velue_width, relheight=velue_height, relx=0.1, rely=0.65)


# 快捷啟動顯示
# quick_launch_label = ttk.Label(root, text="應用程式快捷：多個")
# quick_launch_label.pack(pady=10)

# left_arrow_img = None
# right_arrow_img = None


# 更新 UI
update_listbox()

# 啟動 GUI
root.mainloop()