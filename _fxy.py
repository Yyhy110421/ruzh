import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, r"E:\守译-发布")
from app import bootstrap
bootstrap.prepare()
import tkinter as tk
from app.floating import FloatingWindow
root = tk.Tk(); root.withdraw()
win = FloatingWindow(root)
items = [
    (23, 6, 420, 24, "文件 编辑 查看 仓库 分支 帮助"),
    (81, 124, 398, 60, "让我们开始吧！"),
    (82, 188, 420, 25, "在 GitHub Desktop 中添加一个仓库以开始协作"),
    (107, 266, 173, 23, "过滤你的仓库"),
    (681, 282, 210, 30, "创建教程仓库..."),
    (95, 314, 134, 22, "您的仓库"),
    (97, 349, 154, 27, "Yyhy110421/ruzh"),
    (683, 367, 275, 25, "克隆互联网上的仓库..."),
    (682, 450, 331, 23, "创建本地驱动盘上的新仓库..."),
    (680, 528, 380, 23, "添加本地驱动盘上的现有仓库..."),
]
win.update_result("File Edit View Repository Branch Help Lets get started Add a repository to GitHub Desktop", items)
for _ in range(20):
    root.update(); time.sleep(0.05)
print("  geom:", win.winfo_geometry(), flush=True)
end = time.time() + 40
while time.time() < end:
    root.update(); time.sleep(0.2)
