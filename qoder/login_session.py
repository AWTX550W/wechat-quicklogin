#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登录会话：可见 Edge 窗口打开目标站登录页，保持存活等扫码确认。
- 拦截微信 /connect/qrcode/ 响应存干净 png；拦截不到则截屏+WeChatCV 解码裁切。
- 每 2s 检查 URL 是否离开登录页 → 记 logged_in 并截图。
- 不点任何按钮、不发消息、不碰 Cookie。
用法: python login_session.py --url https://www.nowcoder.com/login --tag nowcoder --hold 600
"""
from __future__ import annotations
import argparse, asyncio, datetime, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EVID = os.path.join(ROOT, "evidence")
sys.path.insert(0, os.path.join(ROOT, "desktop-scan"))

async def run(url: str, tag: str, hold: int, visible: bool, preclick: str = ""):
    from playwright.async_api import async_playwright
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    status_path = os.path.join(EVID, f"day4_session_{tag}.json")
    status = {"tag": tag, "ts": ts, "url": url, "state": "starting",
              "qr_png": None, "payload": None, "events": []}

    def save():
        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            channel="msedge", headless=not visible,
            args=["--no-first-run", "--no-default-browser-check", "--start-maximized"])
        ctx = await browser.new_context(viewport={"width": 1280, "height": 860})
        page = await ctx.new_page()

        async def on_resp(resp):
            u = resp.url
            if "/connect/qrcode/" in u:
                try:
                    body = await resp.body()
                    p = os.path.join(EVID, f"day4_liveqr_{tag}_{ts}.png")
                    with open(p, "wb") as f:
                        f.write(body)
                    uuid = u.rsplit("/", 1)[-1].split("?")[0]
                    status["qr_png"] = p
                    status["payload"] = f"https://open.weixin.qq.com/connect/confirm?uuid={uuid}"
                    status["events"].append(f"[qrcode] {uuid}")
                    save()
                except Exception as e:
                    status["events"].append(f"[qrcode fail] {e}")

        page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            status["events"].append(f"[goto] {type(e).__name__} {str(e)[:150]}")
        if preclick:
            for kw in preclick.split(","):
                try:
                    loc = page.get_by_text(kw.strip(), exact=False).first
                    await loc.wait_for(state="visible", timeout=8000)
                    await loc.click()
                    status["events"].append(f"[preclick] {kw}")
                    await page.wait_for_timeout(2500)
                    break
                except Exception as e:
                    status["events"].append(f"[preclick fail] {kw} {type(e).__name__}")
        login_url = page.url
        status["state"] = "waiting_scan"
        save()

        # 截屏兜底解码（站点自绘码 / 拦截不到时）
        async def fallback_decode():
            if status["qr_png"]:
                return
            try:
                import scan_helper as sh
                from PIL import Image
                shot = os.path.join(EVID, f"day4_page_{tag}_{ts}.png")
                await page.screenshot(path=shot)
                payloads, crop = sh.decode_pil(Image.open(shot).convert("RGB"))
                if payloads:
                    status["payload"] = payloads[0]
                    qr = crop or Image.open(shot)
                    qr, _, _ = sh.prepare_for_wechat(qr)
                    p = os.path.join(EVID, f"day4_liveqr_{tag}_{ts}.png")
                    qr.save(p)
                    status["qr_png"] = p
                    status["events"].append("[fallback-decode] " + payloads[0][:120])
                    save()
            except Exception as e:
                status["events"].append(f"[fallback fail] {type(e).__name__} {str(e)[:120]}")

        await page.wait_for_timeout(6000)
        await fallback_decode()

        t0 = asyncio.get_event_loop().time()
        last_shot = 0
        while asyncio.get_event_loop().time() - t0 < hold:
            await page.wait_for_timeout(2000)
            cur = page.url
            if cur != login_url and "login" not in cur.lower() and "passport" not in cur.lower():
                status["state"] = "logged_in"
                status["final_url"] = cur
                await page.screenshot(path=os.path.join(EVID, f"day4_loggedin_{tag}_{ts}.png"))
                save()
                break
            # 每 30s 兜底解码一次（码刷新时更新 png）
            if asyncio.get_event_loop().time() - last_shot > 30:
                last_shot = asyncio.get_event_loop().time()
                await fallback_decode()
        else:
            status["state"] = "timeout"
            await page.screenshot(path=os.path.join(EVID, f"day4_timeout_{tag}_{ts}.png"))
        save()
        # 登录成功后多留 60s 让用户看见已登录界面
        if status["state"] == "logged_in":
            await page.wait_for_timeout(60000)
        await browser.close()
    print(json.dumps(status, ensure_ascii=False))
    return 0 if status["state"] == "logged_in" else 1

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--hold", type=int, default=600)
    ap.add_argument("--visible", action="store_true")
    ap.add_argument("--preclick", default="", help="加载后点一次的登录入口文案，逗号分隔")
    a = ap.parse_args()
    return asyncio.run(run(a.url, a.tag, a.hold, a.visible, a.preclick))

if __name__ == "__main__":
    sys.exit(main())