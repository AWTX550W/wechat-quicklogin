#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
决定性实验：真实 Edge 打开真实站点，走微信快捷登录，抓 iframe 对
https://localhost.weixin.qq.com:*/api/* 的真实请求与错误。

只读性质：不发送任何消息、不点"同意授权/确认登录"以外的按钮、不碰 Cookie。
唯一副作用是页面自身的正常网络请求。

用法：
  python evidence/probe_quicklogin.py --lna off --url https://channels.weixin.qq.com/login.html
  python evidence/probe_quicklogin.py --lna on  --url https://docs.qq.com/desktop
  python evidence/probe_quicklogin.py --click        # 额外尝试点"微信快捷登录"/"立即登录"

输出：evidence/probe_<时间戳>_<lna>.txt，并保存同名 .png 截图。
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "evidence")

LNA_RELATED = (
    "localhost.weixin.qq.com",
    "127.0.0.1:1",
    "open.weixin.qq.com",
)


def is_wx_local(url: str) -> bool:
    return "localhost.weixin.qq.com" in url or "://127.0.0.1:14" in url


async def _count_kw(page, kw: str) -> int:
    cnt = 0
    for f in page.frames:
        try:
            cnt += await f.get_by_text(kw, exact=False).count()
        except Exception:  # noqa: BLE001
            pass
    return cnt


async def run(url: str, lna: str, do_click: bool, wait_ms: int, hold: int) -> int:
    from playwright.async_api import async_playwright

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_txt = os.path.join(EVID, f"probe_{ts}_lna-{lna}.txt")
    out_png = os.path.join(EVID, f"probe_{ts}_lna-{lna}.png")
    lines: list[str] = []

    def log(*a):
        s = " ".join(str(x) for x in a)
        lines.append(s)
        print(s)

    async with async_playwright() as pw:
        args = ["--no-first-run", "--no-default-browser-check"]
        if lna == "off":
            # 仅本次临时进程内禁用本地网络访问检查，不改系统/浏览器配置
            args.append("--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessNewFeature,"
                        "BlockInsecurePrivateNetworkRequests")
        browser = await pw.chromium.launch(channel="msedge", headless=True, args=args)
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        page.on("request", lambda r: is_wx_local(r.url) and log("[REQ ]", r.method, r.url[:190]))
        page.on("response", lambda r: is_wx_local(r.url) and log("[RESP]", r.status, r.url[:190]))
        page.on("requestfailed", lambda r: is_wx_local(r.url) and log("[FAIL]", r.url[:190], "->", r.failure))
        page.on("console", lambda m: ("weixin" in (m.text or "").lower() or "local network" in (m.text or "").lower()) and log("[CON]", m.type, m.text[:300]))

        # 抓 response body（check-login / authorize 最关键）
        bodies: dict[str, str] = {}

        async def grab_body(resp):
            if not is_wx_local(resp.url):
                return
            try:
                b = await resp.body()
                bodies[resp.url] = b[:2000].decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                pass

        page.on("response", lambda r: asyncio.ensure_future(grab_body(r)))

        log("浏览器: Edge(channel=msedge) headless   LNA:", lna)
        log("目标  :", url)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:  # noqa: BLE001
            log("[WARN] goto:", type(e).__name__, str(e)[:200])

        await page.wait_for_timeout(min(wait_ms, 15000))

        # 页面/iframe 结构
        log("\n--- frames ---")
        for f in page.frames:
            log(f"  frame url={f.url[:150]}")
        log("\n--- iframe 标签（看 allow 属性）---")
        try:
            html = await page.content()
            import re
            for m in re.findall(r"<iframe[^>]*>", html, re.I)[:12]:
                log("  " + m[:400])
        except Exception:  # noqa: BLE001
            pass

        # 页面上可见的关键文案，判断到底是二维码态还是头像+快捷登录态
        log("\n--- 关键文案探测 ---")
        for kw in ["微信快捷登录", "使用微信扫码", "立即登录", "同意", "扫码登录", "二维码"]:
            hit = 0
            for f in page.frames:
                try:
                    hit += await f.get_by_text(kw, exact=False).count()
                except Exception:  # noqa: BLE001
                    pass
            log(f"  '{kw}' 命中 {hit}")

        try:
            await page.screenshot(path=out_png, full_page=False)
        except Exception:  # noqa: BLE001
            pass

        if do_click:
            log("\n--- 登录 iframe 内部结构（截断）---")
            for f in page.frames:
                if "qrconnect" in f.url:
                    try:
                        inner = await f.content()
                        log("  len=%d" % len(inner))
                        log("  " + inner[:1800].replace("\n", " "))
                    except Exception as e:  # noqa: BLE001
                        log("  取 iframe 内容失败:", type(e).__name__, str(e)[:120])

            log("\n--- 尝试点击流程 ---")
            shots_dir = os.path.join(EVID, f"shots_{ts}")
            stop_ev = threading.Event()

            def sample():
                os.makedirs(shots_dir, exist_ok=True)
                t0 = time.time()
                while not stop_ev.is_set() and time.time() - t0 < 45:
                    try:
                        from PIL import ImageGrab
                        img = ImageGrab.grab(all_screens=True)
                        img.save(os.path.join(shots_dir, f"t{int((time.time() - t0) * 10):03d}.png"))
                    except Exception as e:  # noqa: BLE001
                        log("  [shot fail]", type(e).__name__, str(e)[:90])
                        break
                    time.sleep(1.2)

            th = threading.Thread(target=sample, daemon=True)
            th.start()

            for step_kw in ["立即登录", "同意", "微信快捷登录"]:
                clicked = False
                for f in page.frames:
                    try:
                        loc = f.get_by_text(step_kw, exact=False)
                        n = await loc.count()
                        if n == 0:
                            continue
                        for i in range(min(n, 4)):
                            el = loc.nth(i)
                            try:
                                box = await el.bounding_box()
                            except Exception:  # noqa: BLE001
                                box = None
                            log(f"  '{step_kw}' #{i} box={box} visible={await el.is_visible()} "
                                f"html={(await el.evaluate('e => e.outerHTML'))[:120]}")
                            if box and box["width"] > 1:
                                await page.mouse.click(box["x"] + box["width"] / 2,
                                                       box["y"] + box["height"] / 2)
                                log(f"  -> mouse click @ {box['x'] + box['width']/2:.0f},"
                                    f"{box['y'] + box['height']/2:.0f}")
                                clicked = True
                                await page.wait_for_timeout(4000)
                                break
                        if not clicked:
                            try:
                                await loc.first.evaluate("e => e.click()")
                                log(f"  -> DOM click '{step_kw}'")
                                clicked = True
                                await page.wait_for_timeout(4000)
                            except Exception as e:  # noqa: BLE001
                                log(f"  DOM click 失败: {type(e).__name__} {str(e)[:120]}")
                    except Exception as e:  # noqa: BLE001
                        log(f"  '{step_kw}' 探测异常: {type(e).__name__} {str(e)[:140]}")
                    if clicked:
                        break
                if not clicked:
                    log(f"  '{step_kw}' 无可点击元素")
                await page.wait_for_timeout(1500)

            log("\n--- 点击后保持 %d 秒，观察是否出现确认页/跳转 ---" % hold)
            for i in range(int(hold / 2)):
                await page.wait_for_timeout(2000)
                log(f"  [t+{(i + 1) * 2}s] url={page.url[:90]}  quick登录按钮命中="
                    f"{await _count_kw(page, '微信快捷登录')}")
            stop_ev.set()

            log("\n--- 点击后状态 ---")
            for kw in ["微信快捷登录", "允许", "授权", "扫码", "确认"]:
                cnt = 0
                for f in page.frames:
                    try:
                        cnt += await f.get_by_text(kw, exact=False).count()
                    except Exception:  # noqa: BLE001
                        pass
                log(f"  '{kw}' 命中 {cnt}")
            try:
                await page.screenshot(path=out_png.replace(".png", "_after.png"))
            except Exception:  # noqa: BLE001
                pass

        log("\n--- 捕获到的本地接口响应体 ---")
        for u, b in bodies.items():
            log(f"  {u[:150]}\n    -> {b[:600]}")

        log("\n--- 最终 URL ---")
        log("  " + page.url)
        await browser.close()

    with open(out_txt, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print("\n报告:", out_txt)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://channels.weixin.qq.com/login.html")
    ap.add_argument("--lna", choices=["on", "off"], default="off")
    ap.add_argument("--click", action="store_true")
    ap.add_argument("--wait", type=int, default=20000)
    ap.add_argument("--hold", type=int, default=24, help="点击后保持观察的秒数")
    a = ap.parse_args()
    try:
        return asyncio.run(run(a.url, a.lna, a.click, a.wait, a.hold))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        print("FAIL:", type(e).__name__, e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
