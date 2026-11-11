#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端验证：用真实 Edge(headless) 加载未打包扩展，打开 mock 登录页，
检查 content.js 是否真的把 allow="local-network-access ..." 写进了 DOM。

不需要 Playwright / Puppeteer，只用 Python 标准库 + 本机 Edge。
运行：python evidence/e2e_headless.py
退出码 0 = 通过，1 = 未通过。
"""
from __future__ import annotations

import http.server
import os
import re
import socketserver
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT = os.path.join(ROOT, "extension")
EDGE_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]
PORT = 8765
URL = f"http://127.0.0.1:{PORT}/evidence/mock_login.html"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    browser = next((p for p in EDGE_CANDIDATES if os.path.exists(p)), None)
    if not browser:
        print("FAIL: 未找到 Edge/Chrome，无法跑端到端")
        return 1
    if not os.path.exists(os.path.join(EXT, "manifest.json")):
        print("FAIL: 扩展目录不存在")
        return 1

    os.chdir(ROOT)
    srv = socketserver.TCPServer(("127.0.0.1", PORT), Quiet)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    profile = tempfile.mkdtemp(prefix="wxql_e2e_")

    cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run",
           "--no-default-browser-check",
           "--disable-extensions-except=" + EXT, "--load-extension=" + EXT,
           "--user-data-dir=" + profile, "--virtual-time-budget=6000",
           "--dump-dom", URL]
    print("浏览器 :", browser)
    print("扩展   :", EXT)
    print("页面   :", URL)
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=90)
    except Exception as e:  # noqa: BLE001
        print("FAIL: 浏览器启动失败:", type(e).__name__, e)
        srv.shutdown()
        return 1
    srv.shutdown()

    dom = p.stdout.decode("utf-8", "replace")
    frames = re.findall(r"<iframe[^>]*>", dom, re.I)
    print("\n--- dump-dom 中的 iframe（修复前 mock 页面均无 allow）---")
    for f in frames:
        print("  ", f[:420])

    wx_frames = [f for f in frames if "qrconnect" in f]
    ad_frames = [f for f in frames if "example.com/ad.html" in f]
    patched = [f for f in wx_frames if "local-network-access" in f]
    badge = "__wxql_host__" in dom

    checks = [
        ("dump-dom 有内容", len(dom) > 200),
        ("检测到 2 个微信登录 iframe（同步+异步）", len(wx_frames) == 2),
        ("2 个微信登录 iframe 全部补上 allow", len(patched) == 2),
        ("allow 含 Chrome145 新权限名 loopback-network", all("loopback-network" in f for f in patched)),
        ("普通 iframe 未被误改", len(ad_frames) == 1 and "allow=" not in ad_frames[0]),
        ("状态条已注入", badge),
        ("补属性后自动重载已完成（无残留 reloading 标记）", "data-wxql-reloading" not in dom),
        ("重载后 iframe src 仍是原登录地址", all("qrconnect" in f for f in wx_frames)),
    ]
    print("\n--- 校验 ---")
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    all_ok = all(ok for _, ok in checks)
    print("\n结论:", "端到端通过" if all_ok else "端到端未通过")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
