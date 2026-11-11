#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把「网站应用快速登录」真跑通：打开站点 → 点「微信快捷登录」→ 等用户在微信里点「允许」
→ 抓回调页 → 存 storage_state 供后续验证。

安全边界：不发送/不输入任何消息，不碰 Cookie 内容，唯一的"确认"动作由用户本人在微信里点。

用法：
  python evidence/quicklogin_complete.py --url https://channels.weixin.qq.com/login.html
  python evidence/quicklogin_complete.py --ext --url https://channels.weixin.qq.com/login.html
  python evidence/quicklogin_complete.py --verify        # 用已存的 storage_state 复查登录态
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "evidence")
PROFILE = os.path.join(ROOT, "edge-profile")
STATE = os.path.join(EVID, "wxql_state.json")
EXT = os.path.join(ROOT, "extension")
STATUS = os.path.join(EVID, "quicklogin_status.json")


def write_status(**kw):
    try:
        with open(STATUS, "w", encoding="utf-8") as fh:
            json.dump({**kw, "ts": datetime.datetime.now().isoformat(timespec="seconds")},
                      fh, ensure_ascii=False, indent=2)
    except Exception:  # noqa: BLE001
        pass


def is_wx_local(u: str) -> bool:
    return "localhost.weixin.qq.com" in u or "://127.0.0.1:14" in u


async def run(url: str, use_ext: bool, lna_off: bool, timeout: int) -> int:
    from playwright.async_api import async_playwright

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(EVID, f"complete_{ts}.log")
    fh = open(log_path, "w", encoding="utf-8")

    def log(*a):
        s = " ".join(str(x) for x in a)
        fh.write(s + "\n")
        fh.flush()
        print(s, flush=True)

    os.makedirs(PROFILE, exist_ok=True)
    args = ["--no-first-run", "--no-default-browser-check",
            "--window-size=1280,900", "--window-position=120,60"]
    if lna_off:
        args.append("--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessNewFeature,"
                    "BlockInsecurePrivateNetworkRequests")
    if use_ext:
        args += ["--disable-extensions-except=" + EXT, "--load-extension=" + EXT]

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            PROFILE, channel="msedge", headless=False, args=args,
            viewport=None, ignore_default_args=["--hide-scrollbars"],
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        page.on("request", lambda r: is_wx_local(r.url) and log("[REQ]", r.method, r.url[:160]))
        page.on("response", lambda r: is_wx_local(r.url) and log("[RESP]", r.status, r.url[:160]))
        page.on("requestfailed", lambda r: is_wx_local(r.url) and log("[FAIL]", r.url[:160], r.failure))
        page.on("console", lambda m: "weixin" in (m.text or "").lower() and log("[CON]", m.text[:220]))

        log(f"profile={PROFILE} ext={use_ext} lna_off={lna_off}")
        log("打开:", url)
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(6000)

        # 点击「微信快捷登录」：优先按包围盒真点，退化到 DOM click
        clicked = False
        for f in ctx.pages[0].frames:
            if "qrconnect" not in f.url:
                continue
            try:
                loc = f.get_by_text("微信快捷登录", exact=False)
                n = await loc.count()
            except Exception:  # noqa: BLE001
                n = 0
            for i in range(n):
                try:
                    box = await loc.nth(i).bounding_box()
                except Exception:  # noqa: BLE001
                    box = None
                if box and box["width"] > 4:
                    await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    log(f"[CLICK] 按钮 #{i} @ {box['x'] + box['width']/2:.0f},{box['y'] + box['height']/2:.0f}")
                    clicked = True
                    break
            if clicked:
                break
        if not clicked:
            log("[CLICK] 按包围盒没点到，尝试 DOM click")
            for f in page.frames:
                if "qrconnect" in f.url:
                    try:
                        await f.get_by_text("微信快捷登录", exact=False).first.evaluate("e => e.click()")
                        clicked = True
                        log("[CLICK] DOM click 已执行")
                    except Exception as e:  # noqa: BLE001
                        log("[CLICK] DOM click 失败:", type(e).__name__)

        if not clicked:
            log("[STOP] 找不到/点不到快捷登录按钮（页面可能退回了二维码）")
            await page.screenshot(path=os.path.join(EVID, f"complete_{ts}_nobtn.png"))
            write_status(state="no_button", log=log_path)
            await ctx.close()
            return 1

        log("[WAIT] 已发出 authorize 请求，等你在微信里点「允许」……")
        write_status(state="waiting_confirm", log=log_path, url=page.url)

        deadline = asyncio.get_event_loop().time() + timeout
        last_note = ""
        while asyncio.get_event_loop().time() < deadline:
            await page.wait_for_timeout(2000)
            cur = page.url
            if "oauth-callback" in cur or ("channels.weixin.qq.com" in cur and "login" not in cur):
                log("[OK] 已跳转到回调页:", cur[:160])
                await page.wait_for_timeout(4000)
                await page.screenshot(path=os.path.join(EVID, f"complete_{ts}_loggedin.png"))
                await ctx.storage_state(path=STATE)
                log("[OK] 登录态已保存:", STATE, " 最终页:", page.url[:160])
                write_status(state="success", log=log_path, url=page.url, state_file=STATE)
                await ctx.close()
                return 0
            try:
                cnt = await page.get_by_text("微信快捷登录", exact=False).count()
            except Exception:  # noqa: BLE001
                cnt = -1
            note = f"url={cur[:80]} btn={cnt}"
            if note != last_note:
                log("  [..]", note)
                last_note = note
                write_status(state="waiting_confirm", log=log_path, url=cur, btn=cnt)
            # 页面自身若已显示登录态（头像/工作台），也算成功
            try:
                if await page.get_by_text("退出登录", exact=False).count() > 0:
                    log("[OK] 页面出现「退出登录」，判定已登录")
                    await ctx.storage_state(path=STATE)
                    await page.screenshot(path=os.path.join(EVID, f"complete_{ts}_loggedin.png"))
                    write_status(state="success", log=log_path, url=cur, state_file=STATE)
                    await ctx.close()
                    return 0
            except Exception:  # noqa: BLE001
                pass

        log("[TIMEOUT] 等超时，未确认")
        await page.screenshot(path=os.path.join(EVID, f"complete_{ts}_timeout.png"))
        write_status(state="timeout", log=log_path, url=page.url)
        await ctx.close()
        return 2


async def verify(url: str) -> int:
    from playwright.async_api import async_playwright

    if not os.path.exists(STATE):
        print("没有 storage_state:", STATE)
        return 1
    async with async_playwright() as pw:
        b = await pw.chromium.launch(channel="msedge", headless=True)
        ctx = await b.new_context(storage_state=STATE)
        page = await ctx.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(8000)
        btn = await page.get_by_text("微信快捷登录", exact=False).count()
        logout = 0
        for kw in ("退出登录", "工作台", "发表"):
            try:
                logout += await page.get_by_text(kw, exact=False).count()
            except Exception:  # noqa: BLE001
                pass
        print("URL      :", page.url)
        print("快捷登录按钮:", btn, " 登录后标志:", logout)
        print("结论      :", "已登录" if btn == 0 and logout > 0 else "仍是未登录态")
        await page.screenshot(path=os.path.join(EVID, "verify_state.png"))
        await b.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://channels.weixin.qq.com/login.html")
    ap.add_argument("--ext", action="store_true")
    ap.add_argument("--lna-off", action="store_true")
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--verify", action="store_true")
    a = ap.parse_args()
    if a.verify:
        return asyncio.run(verify(a.url))
    return asyncio.run(run(a.url, a.ext, a.lna_off, a.timeout))


if __name__ == "__main__":
    sys.exit(main())
