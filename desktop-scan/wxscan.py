#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面一键扫码窗（无黑框）

双击桌面的「扫码登录.bat」-> 直接弹出这个置顶小窗：
  启动即自动截屏识别屏幕上的微信登录码，放大显示出来，图片同时进剪贴板。
  点「用微信扫这个码」-> 自动把 PC 微信拉到前台并 Ctrl+V。
  剩下的确认动作只能由微信客户端发出（安全模型决定的硬边界，不伪造、不绕过）。

全部本地运行，不联网、不上传图片、不碰账号与凭证。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import time
import tkinter as tk
from tkinter import ttk, filedialog

from PIL import Image, ImageTk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan_helper as sh  # noqa: E402  识别 / 裁切 / 放大 / 剪贴板

BG, PANEL, FG, MUTED = "#12161f", "#1b2230", "#e8edf5", "#8d9aad"
OK, WARN, BAD = "#3ddc84", "#f0b429", "#f0616d"


# 关于高 DPI：本机是 150% 缩放。实测给 tkinter 声明 PER_MONITOR_AWARE_V2 之后，
# geometry 会被再除一次缩放因子（420x620 实际只给 295x451），文字换行挤爆、按钮被裁，
# 比不声明更糟。所以这里故意不声明 DPI 感知：由系统把整窗位图等比拉伸，
# 布局比例保持正确，只是字体略软；二维码是黑白块，拉伸后反而更清楚。
# 相应地窗口逻辑尺寸做小一点（380x560），拉伸 1.5 倍后约 570x840，不占满屏幕。


# ------------------------------ Win32：找到微信、置前、粘贴 ------------------------------
# 2026-11-10 修复：微信最小化时主窗口 rect 是 (-32000,-32000) 237x39，
# 旧实现只 ShowWindow + SetForegroundWindow 且吞掉失败、恒返回 True，
# 于是 Ctrl+V 会发给前台的别的窗口（WorkBuddy / 浏览器），粘贴静默落空。
class _RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


def _win_rect(u, hwnd):
    r = _RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    return r.l, r.t, r.r - r.l, r.b - r.t


def find_wechat_window():
    """返回微信主窗口 hwnd；多个候选时取面积最大的，排除最小化占位小窗。"""
    u = ctypes.windll.user32
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    hits = []

    def cb(hwnd, _lparam):
        if u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            if title and ("微信" in title or title in ("WeChat", "Weixin")):
                _, _, w, h = _win_rect(u, hwnd)
                # 最小化时 w/h 是 237x39；仍要收进候选（否则找不到），但按面积排最后
                hits.append((max(w, 0) * max(h, 0), hwnd))
        return True

    u.EnumWindows(PROC(cb), 0)
    if not hits:
        return None
    hits.sort(reverse=True)
    return hits[0][1]


def bring_wechat_front():
    """真正把微信恢复到前台；返回是否成功抢到前台（不再恒 True）。"""
    hwnd = find_wechat_window()
    if not hwnd:
        return False
    u = ctypes.windll.user32
    if u.IsIconic(hwnd):
        u.ShowWindow(hwnd, 9)   # SW_RESTORE（最小化时必须）
    else:
        u.ShowWindow(hwnd, 5)   # SW_SHOW
    # 临时置顶，避免宿主/其它窗口把坐标点击与键盘焦点抢走
    u.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001 | 0x0040)
    ok = False
    for _ in range(4):
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        time.sleep(0.15)
        if u.GetForegroundWindow() == hwnd:
            ok = True
            break
        # ALT 短按可解除 Windows 的前台锁定
        u.keybd_event(0x12, 0, 0, 0)
        u.keybd_event(0x12, 0, 0x0002, 0)
    time.sleep(0.25)  # 等窗口真正就绪再接受 Ctrl+V
    return ok


def release_wechat_topmost():
    """用完取消置顶，别让微信永远压在其它窗口上。"""
    hwnd = find_wechat_window()
    if hwnd:
        ctypes.windll.user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0002 | 0x0001)


def send_ctrl_v():
    u = ctypes.windll.user32
    VK_CONTROL, VK_V, KEYUP = 0x11, 0x56, 0x0002
    u.keybd_event(VK_CONTROL, 0, 0, 0)
    u.keybd_event(VK_V, 0, 0, 0)
    u.keybd_event(VK_V, 0, KEYUP, 0)
    u.keybd_event(VK_CONTROL, 0, KEYUP, 0)


# ------------------------------ 窗口 ------------------------------
class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.qr_img = None
        self.payload = ""
        self.photo = None
        self.pinned = tk.BooleanVar(value=True)

        root.title("微信扫码 · 电脑确认")
        root.configure(bg=BG)
        root.geometry("380x670+60+40")
        root.resizable(False, False)
        root.attributes("-topmost", True)

        tk.Label(root, text="微信扫码 · 电脑确认", bg=BG, fg=FG,
                 font=("Microsoft YaHei", 15, "bold")).pack(anchor="w", padx=18, pady=(14, 2))
        tk.Label(root, text="双击即自动抓屏幕上的登录码，不用按热键、不用切来切去",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei", 9)).pack(anchor="w", padx=18, pady=(0, 10))

        self.status = tk.Label(root, text="正在识别屏幕上的二维码…", bg=PANEL, fg=WARN,
                               font=("Microsoft YaHei", 10), wraplength=344,
                               justify="left", padx=10, pady=8, anchor="w")
        self.status.pack(fill="x", padx=16)

        card = tk.Frame(root, bg="white", width=260, height=260)
        card.pack(pady=12)
        card.pack_propagate(False)
        self.qr_label = tk.Label(card, text="（等待二维码）", bg="white", fg="#666666",
                                 font=("Microsoft YaHei", 10))
        self.qr_label.pack(expand=True)

        self.payload_label = tk.Label(root, text="—", bg=BG, fg=MUTED,
                                      font=("Consolas", 9), wraplength=344, justify="left")
        self.payload_label.pack(anchor="w", padx=18)

        btn = tk.Frame(root, bg=BG)
        btn.pack(fill="x", padx=16, pady=(12, 4))
        self.go = tk.Button(btn, text="① 用微信扫这个码", command=self.one_click,
                            bg="#1a7f4b", fg="white", activebackground="#249c5e",
                            relief="flat", font=("Microsoft YaHei", 11, "bold"), height=2,
                            cursor="hand2")
        self.go.pack(fill="x")
        row = tk.Frame(root, bg=BG)
        row.pack(fill="x", padx=16)
        tk.Button(row, text="重新抓一次", command=self.rescan, bg=PANEL, fg=FG,
                  relief="flat", font=("Microsoft YaHei", 9), cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(row, text="选一张图", command=self.pick_image, bg=PANEL, fg=FG,
                  relief="flat", font=("Microsoft YaHei", 9), cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(row, text="复制链接", command=self.copy_link, bg=PANEL, fg=FG,
                  relief="flat", font=("Microsoft YaHei", 9), cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Checkbutton(row, text="置顶", variable=self.pinned, command=self.toggle_pin,
                       bg=BG, fg=MUTED, selectcolor=PANEL, activebackground=BG,
                       font=("Microsoft YaHei", 9)).pack(side="right")

        tip = ("★ 弹出「授权/确认登录」→ 点确认即成；只弹「前往」→ 只能用手机扫。\n"
               "更快：微信 Alt+A 框住这张码，点二维码图标。")
        tk.Label(root, text=tip, bg=BG, fg=MUTED, font=("Microsoft YaHei", 9),
                 wraplength=344, justify="left").pack(anchor="w", padx=18, pady=(8, 10))

    # ---------- 状态 ----------
    def say(self, msg, color=FG):
        self.status.configure(text=msg, fg=color)
        self.root.update_idletasks()

    def toggle_pin(self):
        self.root.attributes("-topmost", bool(self.pinned.get()))

    # ---------- 识别 ----------
    def load(self, img: Image.Image, tag: str):
        payloads, crop = sh.decode_pil(img)
        if not payloads:
            self.say("没抓到二维码。把登录框的码完整露在屏幕上，点「重新抓一次」；也可用「选一张图」。", BAD)
            return False
        self.payload = payloads[0]
        qr = crop or img
        qr, _, scale = sh.prepare_for_wechat(qr)
        self.qr_img = qr
        sh._clipboard_set(image=qr)
        shown = qr.convert("RGB").resize((240, 240), Image.Resampling.NEAREST)
        self.photo = ImageTk.PhotoImage(shown)
        self.qr_label.configure(image=self.photo, text="")
        self.payload_label.configure(text=self.payload[:110])
        kind = sh.classify(self.payload)
        color = OK if kind == "微信登录/授权二维码" else WARN
        self.say(f"✅ 已抓到{kind}，图片已在剪贴板。\n"
                 f"点「① 用微信扫这个码」即可，不用按 Alt+Q。  [{tag}]", color)
        return True

    def rescan(self):
        try:
            img = sh.grab_screen()
        except Exception as e:  # noqa: BLE001
            self.say(f"截屏失败：{type(e).__name__}: {e}。可改用「选一张图」。", BAD)
            return False
        return self.load(img, "自动截屏")

    def pick_image(self):
        p = filedialog.askopenfilename(title="选择含二维码的图片",
                                       filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp"), ("所有文件", "*.*")])
        if p:
            self.load(Image.open(p).convert("RGB"), os.path.basename(p))

    # ---------- 动作 ----------
    def copy_link(self):
        if not self.payload:
            self.say("还没有识别到码。", WARN)
            return
        sh._clipboard_set(text=self.payload)
        self.say("链接已复制。也可直接粘到微信文件传输助手里点开。", OK)

    def one_click(self):
        if self.qr_img is None and not self.rescan():
            return
        sh._clipboard_set(image=self.qr_img)
        # 先取消本窗置顶，否则它会挡住微信、也会抢走键盘焦点
        self.root.attributes("-topmost", False)
        self.pinned.set(False)
        if not bring_wechat_front():
            self.say("没能把 PC 微信拉到前台（微信未启动 / 找不到主窗口 / 前台被别的窗口锁住）。\n"
                     "二维码图已放进剪贴板，请手动切到微信按 Ctrl+V。", WARN)
            return
        time.sleep(0.3)
        send_ctrl_v()
        # 留 1.5 秒让微信保持最上层，用户能看见刚粘上的图；之后自动取消置顶
        self.root.after(1500, release_wechat_topmost)
        self.say("微信已恢复并抢到前台，图片已自动 Ctrl+V。\n"
                 "在微信里右键刚粘贴的图片 →「识别图中二维码」→ 点确认登录。\n"
                 "（本窗已取消置顶，免得挡住微信；要看大码可再勾「置顶」）", OK)


# ------------------------------ 自检（无头，不弹窗） ------------------------------
def selfcheck(image_path: str) -> int:
    import cv2  # noqa: F401  确认 GUI 版依赖齐全
    root = tk.Tk()
    root.withdraw()  # 不显示，避免打扰桌面
    app = App(root)
    checks = []

    if image_path and os.path.isfile(image_path):
        checks.append(("真实截图识别并渲染", app.load(Image.open(image_path).convert("RGB"), "自检")))
    else:
        checks.append(("真实截图识别并渲染", False))
    checks.append(("二维码图片已生成", app.qr_img is not None and min(app.qr_img.size) >= 400))
    checks.append(("微信主窗口可定位", find_wechat_window() is not None))
    checks.append(("剪贴板写入可用", bool(sh._clipboard_set(image=app.qr_img)) if app.qr_img else False))
    checks.append(("payload 分类正确", sh.classify(app.payload) == "微信登录/授权二维码"))
    root.update_idletasks()
    root.destroy()
    # 自检会把测试二维码放进剪贴板，清掉，别给用户留一张过期码
    try:
        u = ctypes.windll.user32
        if u.OpenClipboard(None):
            u.EmptyClipboard()
            u.CloseClipboard()
    except Exception:  # noqa: BLE001
        pass

    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    passed = sum(1 for _, ok in checks if ok)
    print(f"结论: {passed}/{len(checks)} 通过")
    return 0 if passed == len(checks) else 1


def main():
    ap = argparse.ArgumentParser(description="桌面一键扫码窗")
    ap.add_argument("--image", help="启动时识别指定图片（不截屏）")
    ap.add_argument("--selfcheck", help="无头自检：识别 + 渲染 + 窗口定位 + 剪贴板，不弹窗")
    args = ap.parse_args()

    if args.selfcheck:
        return selfcheck(args.selfcheck)
    root = tk.Tk()
    app = App(root)
    if args.image and os.path.isfile(args.image):
        root.after(80, lambda: app.load(Image.open(args.image).convert("RGB"), os.path.basename(args.image)))
    else:
        root.after(80, app.rescan)
    root.mainloop()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001  pythonw 没有控制台，出错必须留痕迹、给提示
        import traceback
        detail = traceback.format_exc()
        try:
            log = os.path.join(os.environ.get("TEMP", HERE), "wxscan_error.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write(detail)
        except Exception:  # noqa: BLE001
            pass
        try:
            import tkinter.messagebox as mb
            mb.showerror("扫码窗启动失败", detail[-900:])
        except Exception:  # noqa: BLE001
            pass
        sys.exit(1)
