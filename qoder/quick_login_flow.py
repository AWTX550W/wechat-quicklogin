#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""网站应用微信登录·快捷登录通道（qrconnect 官方组件，LNA 握手）。
适用：弹窗为 open.weixin.qq.com/connect/qrconnect 的站点（如数控驿站）。
流程：可见 Edge 开站点登录页 → 点微信登录入口 → 弹窗里等「微信快捷登录」→
点它发起 authorize → 微信主窗口弹授权卡 → 【用户本人点允许】→ 回调登录。
本人必点：浏览器 LNA 权限弹窗（如有）、微信「允许」。脚本不代点这两处。
"""
from __future__ import annotations
import argparse, asyncio, datetime, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_proj_evid = r"E:\WorkBuddy\开发\wechat-quicklogin\evidence"
EVID = _proj_evid if os.path.isdir(_proj_evid) else os.path.join(ROOT, "evidence")
os.makedirs(EVID, exist_ok=True)

async def run(url, entry, tag, hold):
    from playwright.async_api import async_playwright
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    status = {"tag": tag, "ts": ts, "state": "starting", "events": []}
    sp = os.path.join(EVID, f"day4_quick_{tag}.json")

    def save():
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    def log(m):
        print(m, flush=True)
        status["events"].append(m)
        save()

    async with async_playwright() as pw:
        # 仅本次临时浏览器进程内放行本地网络探测（Edge142+ 默认静默拦截 localhost 探测，
        # 导致官方「微信快捷登录」按钮永不出现）。不改用户浏览器/系统任何持久配置。
        # --no-proxy-server：微信本地接口只认浏览器直连；走系统代理(Karing等)会被拒。
        # 临时浏览器不走代理，用户日常代理可保持开启。微信侧最终授权卡仍由【本人点允许】。
        browser = await pw.chromium.launch(channel="msedge", headless=False,
            args=["--no-first-run", "--no-default-browser-check", "--start-maximized",
                  "--no-proxy-server",
                  "--disable-features=LocalNetworkAccessChecks,LocalNetworkAccessNewFeature,"
                  "BlockInsecurePrivateNetworkRequests"])
        ctx = await browser.new_context(viewport={"width": 1280, "height": 860})
        page = await ctx.new_page()
        popup = None

        async def on_page(pg):
            nonlocal popup
            popup = pg
            pg.on("console", lambda m: log(f"[pop] {(m.text or '')[:160]}"))
            pg.on("request", lambda r: log(f"[req] {r.method} {r.url[:110]}")
                  if "localhost.weixin" in r.url else None)
            pg.on("requestfailed", lambda r: log(f"[reqfail] {r.url[:110]} {r.failure}")
                  if "localhost.weixin" in r.url else None)
            pg.on("response", lambda r: log(f"[resp] {r.status} {r.url[:110]}")
                  if "localhost.weixin" in r.url else None)
        ctx.on("page", on_page)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await page.wait_for_timeout(3000)
            loc = page.get_by_text(entry, exact=False).first
            await loc.wait_for(state="visible", timeout=10000)
            await loc.click()
            log(f"[entry] clicked {entry}")
            for _ in range(20):
                if popup:
                    break
                await page.wait_for_timeout(500)
            if not popup:
                log("[fail] no popup")
                status["state"] = "no_popup"
                save()
                return 1
            await popup.wait_for_load_state("domcontentloaded", timeout=30000)
            log(f"[popup] {popup.url[:120]}")
            await popup.screenshot(path=os.path.join(EVID, f"day4_quick_popup_{tag}_{ts}.png"))

            # 等快捷登录按钮出现（LNA 放行后才有；本人需先点浏览器权限弹窗）
            btn = None
            t0 = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - t0 < 60:
                for kw in ("微信快捷登录", "快捷登录"):
                    l = popup.get_by_text(kw, exact=False).first
                    try:
                        if await l.count() and await l.is_visible():
                            btn = l
                            break
                    except Exception:
                        pass
                if btn:
                    break
                await popup.wait_for_timeout(1500)
            if not btn:
                await popup.screenshot(path=os.path.join(EVID, f"day4_quick_noquick_{tag}_{ts}.png"))
                if any("仅支持扫一扫" in e for e in status["events"]):
                    log("[limit] 微信本地接口返回10057：此应用仅支持扫一扫登录（应用级限制，快捷登录不可用）")
                    status["state"] = "camera_only_app"
                else:
                    log("[wait] 60s 内未见快捷登录按钮：本地探测未成功（查看 [pop] 日志）")
                    status["state"] = "no_quick_button"
                save()
                await popup.wait_for_timeout(20000)
                return 1
            await btn.click()
            log("[quick] clicked 微信快捷登录；authorize 已发起")
            log("★ 微信主窗口会弹授权卡：请【本人】点击『允许』。脚本不代点。")
            status["state"] = "waiting_allow"
            save()
            # 等回调跳转（本人点允许后）
            t0 = asyncio.get_event_loop().time()
            while asyncio.get_event_loop().time() - t0 < hold:
                await popup.wait_for_timeout(2000)
                u = popup.url
                if "open.weixin.qq.com" not in u:
                    status["state"] = "logged_in"
                    status["final_url"] = u
                    save()
                    await popup.wait_for_timeout(3000)
                    await popup.screenshot(path=os.path.join(EVID, f"day4_quick_loggedin_{tag}_{ts}.png"))
                    log(f"[SUCCESS] {tag} 回调跳转 → {u[:120]}")
                    await popup.wait_for_timeout(30000)
                    return 0
            status["state"] = "timeout"
            save()
            log("[timeout] 未等到回调（微信允许需本人点）")
            return 1
        except Exception as e:
            status["state"] = "error"
            status["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            save()
            log(f"[error] {status['error']}")
            return 1
        finally:
            await browser.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="https://www.sk1z.com/login")
    ap.add_argument("--entry", default="微信登录")
    ap.add_argument("--tag", default="sk1z")
    ap.add_argument("--hold", type=int, default=180)
    a = ap.parse_args()
    return asyncio.run(run(a.url, a.entry, a.tag, a.hold))

if __name__ == "__main__":
    sys.exit(main())