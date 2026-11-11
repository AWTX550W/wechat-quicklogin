#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断 sk1z qrconnect 弹窗：快捷登录按钮为何不出现。
只读：抓请求/控制台/DOM，不点任何授权按钮。
用法: python qoder/quick_diag.py [--lna off]
"""
from __future__ import annotations
import argparse, asyncio, datetime, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "evidence")

async def run(lna):
    from playwright.async_api import async_playwright
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    lines = []
    def log(*a):
        s = " ".join(str(x) for x in a)
        lines.append(s); print(s, flush=True)

    async with async_playwright() as pw:
        args = ["--no-first-run", "--no-default-browser-check", "--start-maximized"]
        if lna == "off":
            args.append("--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessNewFeature,BlockInsecurePrivateNetworkRequests")
        browser = await pw.chromium.launch(channel="msedge", headless=False, args=args)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 860})
        page = await ctx.new_page()
        popup = None
        async def on_page(pg):
            nonlocal popup
            popup = pg
        ctx.on("page", on_page)

        def wire(pg, name):
            pg.on("request", lambda r: ("localhost.weixin" in r.url or "127.0.0.1:1" in r.url) and log(f"[{name} REQ]", r.method, r.url[:160]))
            pg.on("requestfailed", lambda r: ("localhost.weixin" in r.url or "127.0.0.1:1" in r.url) and log(f"[{name} FAIL]", r.url[:160], "->", r.failure))
            pg.on("response", lambda r: ("localhost.weixin" in r.url) and log(f"[{name} RESP]", r.status, r.url[:160]))
            pg.on("console", lambda m: log(f"[{name} CON]", m.type, (m.text or "")[:250]))
        wire(page, "main")

        await page.goto("https://www.sk1z.com/login", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        loc = page.get_by_text("微信登录", exact=False).first
        await loc.wait_for(state="visible", timeout=10000)
        await loc.click()
        for _ in range(20):
            if popup: break
            await page.wait_for_timeout(500)
        if not popup:
            log("[fail] no popup"); await browser.close(); return 1
        wire(popup, "pop")
        await popup.wait_for_load_state("domcontentloaded", timeout=30000)
        log("[popup full url]", popup.url)
        await popup.wait_for_timeout(12000)

        info = await popup.evaluate("""() => {
            const out = {};
            const btn = document.querySelector('.js_quick_login_btn');
            out.btn = btn ? {html: btn.outerHTML.slice(0,200), display: getComputedStyle(btn).display,
                       vis: getComputedStyle(btn).visibility,
                       parentDisplay: btn.parentElement ? getComputedStyle(btn.parentElement).display : null,
                       parentClass: btn.parentElement ? btn.parentElement.className : null} : null;
            const tips = document.querySelector('.js_web_qrcode_tips_fast');
            out.tips = tips ? {html: tips.outerHTML.slice(0,200), display: getComputedStyle(tips).display} : null;
            // 找内联脚本里 fast_login / check-login 相关条件
            const scripts = [...document.querySelectorAll('script:not([src])')].map(s=>s.textContent).join('\\n');
            const grab = (kw) => { const i = scripts.indexOf(kw); return i<0 ? null : scripts.slice(Math.max(0,i-300), i+500); };
            out.fast_login = grab('fast_login');
            out.checklogin = grab('check-login');
            out.qlogin = grab('js_quick_login_btn');
            return out;
        }""")
        log("[DOM]", json.dumps(info, ensure_ascii=False)[:4000])
        await popup.screenshot(path=os.path.join(EVID, f"day4_diag_{lna}_{ts}.png"))
        await popup.wait_for_timeout(3000)
        await browser.close()
    with open(os.path.join(EVID, f"day4_diag_{lna}_{ts}.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lna", choices=["on", "off"], default="on")
    a = ap.parse_args()
    return asyncio.run(run(a.lna))

if __name__ == "__main__":
    sys.exit(main())