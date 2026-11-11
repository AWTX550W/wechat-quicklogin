#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读拿活码：headless Edge 新 profile 打开视频号登录页，LNA 不关 → 回退纯二维码。
拦截 /connect/qrcode/ 响应存 png。不点任何按钮、不发消息、不碰 Cookie。"""
import asyncio, datetime, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "evidence")
URL = "https://channels.weixin.qq.com/login.html"

async def main():
    from playwright.async_api import async_playwright
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    status = {"ts": ts, "qr_png": None, "uuid": None, "events": []}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="msedge", headless=True,
                                           args=["--no-first-run", "--no-default-browser-check"])
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900})
        page = await ctx.new_page()

        async def on_resp(resp):
            u = resp.url
            if "open.weixin.qq.com" in u:
                status["events"].append(f"[RESP] {resp.status} {u[:160]}")
            if "/connect/qrcode/" in u:
                try:
                    body = await resp.body()
                    p = os.path.join(EVID, f"day4_liveqr_{ts}.png")
                    with open(p, "wb") as f:
                        f.write(body)
                    status["qr_png"] = p
                    status["uuid"] = u.rsplit("/", 1)[-1].split("?")[0]
                except Exception as e:
                    status["events"].append(f"[qrcode save fail] {e}")

        page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
        page.on("requestfailed", lambda r: status["events"].append(f"[FAIL] {r.url[:140]} {r.failure}"))
        try:
            await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            status["events"].append(f"[goto] {type(e).__name__} {str(e)[:150]}")
        for _ in range(30):
            await page.wait_for_timeout(1000)
            if status["qr_png"]:
                break
        await page.screenshot(path=os.path.join(EVID, f"day4_qrpage_{ts}.png"))
        status["final_url"] = page.url
        status["frames"] = [f.url[:140] for f in page.frames]
        await browser.close()
    with open(os.path.join(EVID, f"day4_qracq_{ts}.json"), "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if status["qr_png"] else 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))