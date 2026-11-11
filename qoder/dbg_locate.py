#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：页面码 vs 真实屏幕 grab 的尺度/遮挡问题。"""
import asyncio, io, os, sys, time
import cv2
import numpy as np
from PIL import Image, ImageGrab
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "desktop-scan"))
import scan_helper as sh

try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
    print("dpi awareness set", flush=True)
except Exception as e:
    print("dpi fail", e, flush=True)

EVID = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evidence")

async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="msedge", headless=False,
            args=["--no-first-run", "--no-default-browser-check", "--start-maximized"])
        ctx = await browser.new_context(viewport={"width": 1280, "height": 860})
        page = await ctx.new_page()
        await page.goto("https://www.nowcoder.com/login", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(6000)
        await page.bring_to_front()
        time.sleep(0.5)
        shot = await page.screenshot()
        pimg = Image.open(io.BytesIO(shot)).convert("RGB")
        pimg.save(os.path.join(EVID, "dbg_page.png"))
        payloads, crop = sh.decode_pil(pimg)
        print("page decode:", payloads, "crop:", crop.size if crop else None, flush=True)
        g = ImageGrab.grab()
        print("grab size:", g.size, flush=True)
        g.save(os.path.join(EVID, "dbg_screen.png"))
        if crop:
            scr = np.array(g.convert("RGB"))[:, :, ::-1].copy()
            tpl = np.array(crop.convert("RGB"))[:, :, ::-1].copy()
            for s in (1.0, 1.25, 1.5, 1.75, 2.0):
                t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s != 1 else tpl
                res = cv2.matchTemplate(scr, t, cv2.TM_CCOEFF_NORMED)
                _, score, _, loc = cv2.minMaxLoc(res)
                print(f"scale {s}: score={score:.3f} loc={loc}", flush=True)
        await browser.close()

asyncio.run(main())