#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读现场快照：可见窗口 + 微信进程 + 全屏截图。不发任何消息、不点任何按钮。"""
import ctypes, os, sys, subprocess
from datetime import datetime

class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

u = ctypes.windll.user32
PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
rows = []

def cb(hwnd, _lp):
    try:
        if u.IsWindowVisible(hwnd):
            n = u.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                r = RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                rows.append((buf.value, r.left, r.top, r.right - r.left,
                             r.bottom - r.top, bool(u.IsIconic(hwnd))))
    except Exception:
        pass
    return True

u.EnumWindows(PROC(cb), 0)
print("=== visible windows ===")
for t, x, y, w, h, m in rows:
    print(f"{'MIN' if m else '   '} ({x},{y}) {w}x{h}  {t[:80]}")

fg = u.GetForegroundWindow()
buf = ctypes.create_unicode_buffer(512)
u.GetWindowTextW(fg, buf, 512)
print("=== foreground ===")
print(buf.value)

print("=== wechat processes ===")
try:
    out = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq WeChatAppEx.exe", "/FO", "CSV"], text=True)
    print(out[:400])
    out2 = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq Weixin.exe", "/FO", "CSV"], text=True)
    print(out2[:400])
    out3 = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq WeChat.exe", "/FO", "CSV"], text=True)
    print(out3[:400])
except Exception as e:
    print("tasklist failed:", e)

print("=== screen grab ===")
from PIL import ImageGrab
stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "evidence")
out_dir = os.path.abspath(out_dir)
try:
    img = ImageGrab.grab(all_screens=True)
except TypeError:
    img = ImageGrab.grab()
p = os.path.join(out_dir, f"day4_screen_{stamp}.png")
img.save(p)
print("saved:", p, img.size)