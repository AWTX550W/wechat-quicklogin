#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双击 bat → 电脑本地「扫一扫」→ 登录。无需掏手机。

流程（2026-11-10 实测打通，牛客首次真实登录成功）：
  1. Edge 打开站点登录页，等微信二维码出现；
  2. 屏幕定位二维码 → 模拟 Alt+A（微信截图）→ 框选二维码 →
     点截图工具栏的「识别二维码」图标（纯本地识别）；
  3. 微信弹出扫描结果：牛客=已关注故扫码即登录；百职帮=需本人在微信窗口点确认；
  4. 轮询网页 URL 判定登录成功，截图存证。

红线（继承 HANDOFF.md，事故后铁律）：
  * 不往任何微信聊天框发消息/链接/图片（含文件传输助手），不写剪贴板；
  * 不自动点「发送 / 确认授权 / 同意 / 允许」——最终确认必须本人点；
  * 不碰 Cookie / 账号密码；只模拟热键、框选、本地点识码图标。

用法: python wx_quick_login.py --site nowcoder|baicizhan [--hold 300] [--auto-exit]
依赖: 本机 Python313 已备 playwright / opencv-contrib / Pillow / numpy；PC 微信已登录。
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import datetime
import io
import json
import os
import sys
import time

import cv2
import numpy as np
from PIL import Image, ImageGrab

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = r"E:\WorkBuddy\开发\wechat-quicklogin"
EVID = os.path.join(PROJECT, "evidence")
for _p in (HERE, os.path.join(PROJECT, "desktop-scan")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import scan_helper as sh  # noqa: E402

TEMPLATE_CANDIDATES = (
    os.path.join(HERE, "assets", "qr_icon.png"),
    os.path.join(HERE, "qr_icon.png"),
    os.path.join(PROJECT, "qoder", "assets", "qr_icon.png"),
)

SITES = {
    "nowcoder": {"url": "https://www.nowcoder.com/login", "preclick": "",
                 "note": "牛客：已关注其公众号时，扫码即登录，通常无需再点确认。"},
    "baicizhan": {"url": "https://np.baicizhan.com/", "preclick": "登录/注册,登录",
                  "note": "百职帮：扫码后请在弹出的微信窗口里【本人】点击确认登录，脚本不会代点。"},
    "screen": {"url": "", "preclick": "",
               "note": "通用模式：扫当前屏幕上露出的第一个二维码；登录码请在微信窗口本人确认。"},
}

# ---------------- Win32 输入（仅热键/框选/本地点识码图标） ----------------
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
u32 = ctypes.windll.user32
LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP = 0x0002, 0x0004, 0x0008, 0x0010
KEYUP = 0x0002


def _mouse(flags):
    u32.mouse_event(flags, 0, 0, 0, 0)


def click_at(x, y):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    _mouse(LEFTDOWN)
    time.sleep(0.06)
    _mouse(LEFTUP)


def right_click_at(x, y):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    _mouse(RIGHTDOWN)
    time.sleep(0.06)
    _mouse(RIGHTUP)


def drag_to(x0, y0, x1, y1):
    u32.SetCursorPos(int(x0), int(y0))
    time.sleep(0.05)
    _mouse(LEFTDOWN)
    for i in range(1, 11):
        u32.SetCursorPos(int(x0 + (x1 - x0) * i / 10), int(y0 + (y1 - y0) * i / 10))
        time.sleep(0.02)
    _mouse(LEFTUP)


def alt_a():
    u32.keybd_event(0x12, 0, 0, 0)          # Alt down
    u32.keybd_event(0x41, 0, 0, 0)          # A down
    time.sleep(0.05)
    u32.keybd_event(0x41, 0, KEYUP, 0)
    u32.keybd_event(0x12, 0, KEYUP, 0)


def grab() -> Image.Image:
    return ImageGrab.grab()


def cancel_overlay():
    g = grab()
    right_click_at(g.width / 2, g.height / 2)


# ---------------- 视觉定位 ----------------
def _enum_windows():
    import ctypes.wintypes as wt
    results = []
    cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def fn(hwnd, _l):
        results.append(hwnd)
        return True
    u32.EnumWindows(cb(fn), 0)
    return results


def minimize_wechat_browser():
    """把微信内置浏览器（扫描结果等 WeChatAppEx 窗口）最小化，避免遮住登录页。
    只最小化、不关闭，可恢复；不碰聊天主窗口。"""
    import ctypes.wintypes as wt
    k32 = ctypes.windll.kernel32
    for hwnd in _enum_windows():
        if not u32.IsWindowVisible(hwnd):
            continue
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = k32.OpenProcess(0x1000, False, pid.value)
        if not hproc:
            continue
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        k32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(size))
        k32.CloseHandle(hproc)
        if "WeChatAppEx.exe" in buf.value:
            u32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            time.sleep(0.2)


def bgr(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGB"))[:, :, ::-1].copy()


def match_best(screen: np.ndarray, tpl: np.ndarray, scales):
    best = None
    for s in scales:
        t = cv2.resize(tpl, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC) if s != 1 else tpl
        if t.shape[0] >= screen.shape[0] or t.shape[1] >= screen.shape[1]:
            continue
        res = cv2.matchTemplate(screen, t, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(res)
        if best is None or score > best[0]:
            best = (score, loc[0], loc[1], t.shape[1], t.shape[0], s)
    return best


def load_template() -> np.ndarray:
    for p in TEMPLATE_CANDIDATES:
        if os.path.exists(p):
            return bgr(Image.open(p))
    raise FileNotFoundError("找不到识码图标模板 qr_icon.png")


def locate_qr(page_img: Image.Image):
    """在页面截图里解码二维码，再到真实屏幕上多尺度定位它的矩形。"""
    payloads, crop = sh.decode_pil(page_img)
    if not payloads or crop is None:
        print("[dbg] 页面截图未解码到二维码", flush=True)
        return None
    screen = bgr(grab())
    tpl = bgr(crop)
    hit = match_best(screen, tpl, (1.0, 1.25, 1.5, 1.75, 2.0))
    if not hit or hit[0] < 0.6:
        print(f"[dbg] 屏幕定位失败 score={hit[0] if hit else None}（二维码可能被遮挡）", flush=True)
        return None
    score, x, y, w, h, s = hit
    return {"payload": payloads[0], "crop": crop,
            "rect": (x, y, x + w, y + h), "score": round(float(score), 3)}


def do_local_scan(rect):
    """Alt+A → 框选 → 点工具栏识码图标。全程本地，不发送任何东西。"""
    x0, y0, x1, y1 = rect
    alt_a()
    time.sleep(1.0)
    drag_to(x0 - 5, y0 - 5, x1 + 5, y1 + 5)
    time.sleep(0.8)
    g = grab()
    factor = 1228.0 / g.width          # 模板采自 1228 宽的 overlay 空间
    small = cv2.resize(bgr(g), (1228, int(g.height * factor)), interpolation=cv2.INTER_AREA)
    hit = match_best(small, load_template(), (1.0,))
    if not hit or hit[0] < 0.6:
        cancel_overlay()
        raise RuntimeError(f"工具栏识码图标定位失败 score={hit[0] if hit else None}；"
                           "请确认 PC 微信已登录且 Alt+A 截图热键未被占用。")
    score, x, y, w, h, _ = hit
    click_at((x + w / 2) / factor, (y + h / 2) / factor)
    time.sleep(2.0)
    return round(float(score), 3)


# ---------------- 通用模式：扫屏幕上现有的码 ----------------
def run_screen():
    k32 = ctypes.windll.kernel32
    console = k32.GetConsoleWindow()
    try:
        if console:
            u32.ShowWindow(console, 6)  # 最小化本控制台，露出后面的页面
        minimize_wechat_browser()
        time.sleep(0.6)
        g = grab()
        payloads, crop = sh.decode_pil(g)
        for _ in range(4):
            if payloads and crop is not None:
                break
            time.sleep(2)
            g = grab()
            payloads, crop = sh.decode_pil(g)
        if not payloads or crop is None:
            print("屏幕上没识别到二维码。请把码完整露在屏幕最前、别被遮挡，再试。", flush=True)
            return 1
        print(f"屏幕识别到 {len(payloads)} 个码，扫第一个：{payloads[0]}", flush=True)
        if payloads[0].startswith(("https://open.weixin.qq.com/connect/", "http://open.weixin.qq.com/connect/")):
            print("这是微信「网站应用」码：微信规定只能手机摄像头识别，电脑识码必跳限制页，不扫。", flush=True)
            print("电脑免手机的正路：微信自带快捷登录——直接在网站登录弹窗里点「微信快捷登录」，微信弹授权卡后本人点『允许』即可（代理/浏览器拦截导致按钮不出现时，见项目 README 补记四的排查指引）。", flush=True)
            return 1
        hit = match_best(bgr(g), bgr(crop), (1.0,))
        if not hit or hit[0] < 0.8:
            print("码定位失败，请重试。", flush=True)
            return 1
        _, x, y, w, h, _ = hit
        do_local_scan((x, y, x + w, y + h))
        print("扫完了，微信已弹结果窗。是登录码的话请【本人】在该窗口确认；脚本不代点。", flush=True)
        return 0
    finally:
        if console:
            u32.ShowWindow(console, 9)  # 恢复控制台让用户看到结果


# ---------------- 主流程 ----------------
async def run(site: str, hold: int, auto_exit: bool):
    from playwright.async_api import async_playwright
    cfg = SITES[site]
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(EVID, exist_ok=True)
    status_path = os.path.join(EVID, f"day4_session_{site}.json")
    status = {"tag": site, "ts": ts, "url": cfg["url"], "state": "starting",
              "qr_png": None, "payload": None, "events": []}

    def save():
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    def log(msg):
        print(msg, flush=True)
        status["events"].append(msg)
        save()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel="msedge", headless=False,
            args=["--no-first-run", "--no-default-browser-check", "--start-maximized"])
        ctx = await browser.new_context(viewport={"width": 1280, "height": 860})
        page = await ctx.new_page()
        try:
            try:
                await page.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                log(f"[goto] {type(e).__name__} {str(e)[:120]}")
            if cfg["preclick"]:
                for kw in cfg["preclick"].split(","):
                    try:
                        loc = page.get_by_text(kw.strip(), exact=False).first
                        await loc.wait_for(state="visible", timeout=8000)
                        await loc.click()
                        log(f"[preclick] {kw}")
                        await page.wait_for_timeout(2500)
                        break
                    except Exception as e:
                        log(f"[preclick fail] {kw} {type(e).__name__}")
            login_url = page.url

            # 等二维码出现（最多 30s）
            minimize_wechat_browser()
            found = None
            for _ in range(15):
                await page.bring_to_front()
                time.sleep(0.3)
                shot = await page.screenshot()
                found = locate_qr(Image.open(io.BytesIO(shot)).convert("RGB"))
                if found:
                    break
                await page.wait_for_timeout(2000)
            if not found:
                log("[fail] 页面未出现可识别的微信二维码")
                status["state"] = "no_qr"
                save()
                return 1
            qr, _, _ = sh.prepare_for_wechat(found["crop"])
            qr_path = os.path.join(EVID, f"day4_liveqr_{site}_{ts}.png")
            qr.save(qr_path)
            status["qr_png"] = qr_path
            status["payload"] = found["payload"]
            status["state"] = "waiting_scan"
            save()
            log(f"[qr] {found['payload']}  屏幕定位 score={found['score']}")

            # 本地扫一扫
            await page.bring_to_front()
            time.sleep(0.3)
            icon_score = do_local_scan(found["rect"])
            log(f"[scan] 已点识码图标 score={icon_score}；{cfg['note']}")
            if site == "baicizhan":
                log("★ 请现在到弹出的微信窗口里【本人】点击确认登录。脚本不代点。")

            # 轮询登录态
            t0 = time.time()
            while time.time() - t0 < hold:
                await page.wait_for_timeout(2000)
                cur = page.url
                if cur != login_url and "login" not in cur.lower() and "passport" not in cur.lower():
                    status["state"] = "logged_in"
                    status["final_url"] = cur
                    save()
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
                    await page.screenshot(path=os.path.join(EVID, f"day4_loggedin_{site}_{ts}.png"))
                    log(f"[SUCCESS] 已登录 {site} → {cur}")
                    if not auto_exit:
                        input("登录成功，浏览器保持打开。回车关闭浏览器退出…")
                    else:
                        await page.wait_for_timeout(3000)
                    return 0
            status["state"] = "timeout"
            save()
            await page.screenshot(path=os.path.join(EVID, f"day4_timeout_{site}_{ts}.png"))
            log(f"[timeout] {hold}s 内未检测到登录跳转")
            return 1
        except Exception as e:
            try:
                cancel_overlay()
            except Exception:
                pass
            status["state"] = "error"
            status["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            save()
            log(f"[error] {status['error']}")
            return 1
        finally:
            await browser.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", choices=list(SITES), default="screen",
                    help="默认 screen：直接扫屏幕上现有的码，不问话；nowcoder/baicizhan 仅回归测试用")
    ap.add_argument("--hold", type=int, default=300)
    ap.add_argument("--auto-exit", action="store_true", help="成功后不等回车（自动化测试用）")
    a = ap.parse_args()
    if a.site == "screen":
        return run_screen()
    return asyncio.run(run(a.site, a.hold, a.auto_exit))


if __name__ == "__main__":
    sys.exit(main())