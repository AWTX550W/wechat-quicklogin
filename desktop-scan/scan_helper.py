#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面「扫一扫」兜底工具（独立可提取，绿色免安装）

给那些压根没接「快速登录」、只丢一张二维码的老站点用。
把手机上的「打开微信 → 扫一扫 → 对准屏幕」换成：
    按 Alt+Q（或 --once） → 自动截屏 → 本地识别二维码 → 图片进剪贴板 →
    在 PC 微信里 Ctrl+V → 右键「识别图中二维码」 → 电脑上点确认。

全部在本地完成，不联网、不上传图片、不碰账号。

用法:
    python scan_helper.py --selftest     # 负例 + 正例 + 难例 + 真实登录码闭环 四轮自测
    python scan_helper.py --once         # 立即截屏识别一次
    python scan_helper.py --image a.png  # 识别指定图片
    python scan_helper.py               # 常驻，Alt+Q 触发；Ctrl+C 退出
依赖: opencv-python / Pillow / numpy（本机已具备）
"""
from __future__ import annotations

import argparse
import ctypes
import io
import os
import sys
import time
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageGrab

ROOT = os.path.dirname(os.path.abspath(__file__))
# GUI 一键版会把结果目录指到 %TEMP%，避免桌面冒出 out/ 文件夹
OUT = os.environ.get("WXSCAN_OUT") or os.path.join(ROOT, "out")
os.makedirs(OUT, exist_ok=True)

WX_LOGIN_HINTS = (
    "open.weixin.qq.com", "login.weixin.qq.com", "weixin.qq.com",
    "wechat.com", "wx.qq.com", "qrconnect", "fast_login",
)


# ------------------------------ 二维码识别 ------------------------------
# 引擎链（借鉴 QRUtil / qr-pypass 的做法）：腾讯 WeChatCV 优先，标准 QRCodeDetector 回落。
# WeChatCV 对模糊、反色、小图、实拍截图的识别率明显更高；本机 opencv-contrib 已内置模型，
# 无参构造即可用，不引入任何新依赖或模型文件。
_WECHAT_DET = None


def _wechat_detector():
    global _WECHAT_DET
    if _WECHAT_DET is None:
        try:
            _WECHAT_DET = cv2.wechat_qrcode_WeChatQRCode() if hasattr(cv2, "wechat_qrcode_WeChatQRCode") else False
        except Exception:  # noqa: BLE001
            _WECHAT_DET = False
    return _WECHAT_DET or None


def _crop_by_points(img, points, rgb_shape):
    if points is None or len(points) == 0:
        return img
    try:
        p = np.asarray(points[0]).astype(int)
        x0, y0 = max(0, p[:, 0].min() - 12), max(0, p[:, 1].min() - 12)
        x1, y1 = min(rgb_shape[1], p[:, 0].max() + 12), min(rgb_shape[0], p[:, 1].max() + 12)
        if x1 > x0 and y1 > y0:
            return img.crop((x0, y0, x1, y1))
    except Exception:  # noqa: BLE001
        pass
    return img


def decode_pil(img: Image.Image):
    """返回 (payloads, cropped_qr_image or None)。找不到码返回空列表。"""
    rgb = np.array(img.convert("RGB"))
    bgr = rgb[:, :, ::-1].copy()

    # 1) WeChatCV（多码 + 抗退化）
    wechat = _wechat_detector()
    if wechat is not None:
        try:
            strings, points = wechat.detectAndDecode(bgr)
            payloads = [s for s in strings if s]
            if payloads:
                return payloads, _crop_by_points(img, points, rgb.shape)
        except cv2.error:
            pass

    # 2) 标准 QRCodeDetector 多码
    det = cv2.QRCodeDetector()
    try:
        ok, decoded, points, _ = det.detectAndDecodeMulti(bgr)
        payloads = [d for d in (decoded or []) if d]
        if payloads:
            return payloads, _crop_by_points(img, points, rgb.shape)
    except cv2.error:
        pass

    # 3) 单码兜底（部分 OpenCV 版本 multi 接口对单码返回不稳定）
    try:
        data, pts, _ = det.detectAndDecode(bgr)
        if data:
            return [data], _crop_by_points(img, [pts] if pts is not None else None, rgb.shape)
    except cv2.error:
        pass
    return [], None


def decode_legacy(img: Image.Image):
    """修复前用的单一 QRCodeDetector，仅用于回归对比，不参与实际流程。"""
    rgb = np.array(img.convert("RGB"))
    bgr = rgb[:, :, ::-1].copy()
    det = cv2.QRCodeDetector()
    try:
        ok, decoded, _, _ = det.detectAndDecodeMulti(bgr)
        hits = [d for d in (decoded or []) if d]
        if hits:
            return hits
    except cv2.error:
        pass
    try:
        data, _, _ = det.detectAndDecode(bgr)
        return [data] if data else []
    except cv2.error:
        return []


def classify(payload: str) -> str:
    low = payload.lower()
    if any(h in low for h in WX_LOGIN_HINTS):
        return "微信登录/授权二维码"
    if payload.startswith(("http://", "https://")):
        return "普通网址"
    return "文本/其他"


# ------------------------------ 送进微信前的图片预处理 ------------------------------
# 微信 PC 端「识别图中二维码」对过小的图容易漏检，公开实测建议二维码边长 >= 400px。
# 屏幕截出来的登录码常常只有 200~260px，这里整数倍最近邻放大：不插值、不糊边、不丢模块。
WX_MIN_SIDE = 400


def prepare_for_wechat(img: Image.Image):
    """返回 (图片, 是否放大, 倍数)。已经是 >=400px 的图原样返回。"""
    w, h = img.size
    short = min(w, h)
    if short <= 0:
        return img, False, 1
    scale = max(1, min(8, (WX_MIN_SIDE + short - 1) // short))
    if scale == 1:
        return img, False, 1
    return img.resize((w * scale, h * scale), Image.Resampling.NEAREST), True, scale


# ------------------------------ 剪贴板（纯 Win32） ------------------------------
def _clipboard_set(image: Image.Image | None = None, text: str | None = None) -> bool:
    if sys.platform != "win32":
        return False
    u32 = ctypes.windll.user32
    k32 = ctypes.windll.kernel32
    CF_DIB, CF_UNICODETEXT, GMEM_MOVEABLE = 8, 13, 0x0002
    u32.OpenClipboard.argtypes = [ctypes.c_void_p]
    u32.EmptyClipboard.restype = ctypes.c_bool
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]

    handles = []
    if not u32.OpenClipboard(None):
        return False
    try:
        u32.EmptyClipboard()
        if image is not None:
            buf = io.BytesIO()
            image.convert("RGB").save(buf, format="BMP")
            dib = buf.getvalue()[14:]  # 去掉 BITMAPFILEHEADER，剩下就是 DIB
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(dib))
            ptr = k32.GlobalLock(h)
            ctypes.memmove(ptr, dib, len(dib))
            k32.GlobalUnlock(h)
            u32.SetClipboardData(CF_DIB, h)
            handles.append(h)
        if text:
            enc = (text + "\0").encode("utf-16-le")
            h = k32.GlobalAlloc(GMEM_MOVEABLE, len(enc))
            ptr = k32.GlobalLock(h)
            ctypes.memmove(ptr, enc, len(enc))
            k32.GlobalUnlock(h)
            u32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        u32.CloseClipboard()
    return True


# ------------------------------ 一次扫描 ------------------------------
def handle_shot(img: Image.Image, source: str):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    payloads, qr_img = decode_pil(img)

    if not payloads:
        print(f"[{stamp}] {source}: 未识别到二维码。"
              "提示：把二维码放大/完整露出，或改用 --image 传入裁好的图片。")
        return False

    if qr_img is None:
        qr_img = img
    original_size = qr_img.size
    qr_img, upscaled, scale = prepare_for_wechat(qr_img)
    png = os.path.join(OUT, f"qr_{stamp}.png")
    qr_img.save(png)
    txt = os.path.join(OUT, f"qr_{stamp}.txt")
    with open(txt, "w", encoding="utf-8") as f:
        f.write("\n".join(payloads))

    # 只把图片放进剪贴板，避免微信 Ctrl+V 时优先粘成文本
    ok = _clipboard_set(image=qr_img)
    print(f"\n[{stamp}] {source}: 识别到 {len(payloads)} 个二维码")
    for i, p in enumerate(payloads, 1):
        print(f"    {i}. [{classify(p)}] {p[:220]}")
    size_note = (f"  ({original_size[0]}x{original_size[1]} -> {qr_img.size[0]}x{qr_img.size[1]},"
                 f" 按微信识码建议 x{scale} 放大)") if upscaled else f"  ({qr_img.size[0]}x{qr_img.size[1]} px)"
    print(f"    图片: {png}{size_note}")
    print(f"    内容: {txt}")
    print(f"    剪贴板: {'已放入二维码图片' if ok else '写入失败（可手动打开上面的 png）'}")

    if any(classify(p) == "微信登录/授权二维码" for p in payloads):
        print("\n    → 这是微信登录码。接下来在电脑上试，多数情况不用掏手机：")
        print("      ① 最快：切到 PC 微信按 Alt+A 框住屏幕上这个码，点截图工具栏的二维码图标")
        print("      ② 或：文件传输助手 Ctrl+V（图片已在剪贴板）→ 右键「识别图中二维码」")
        print("      ③ 或：微信左下角「扫一扫 → 相册」选上面那张 png")
        print("    ★ 成败判据：弹出「授权 / 确认登录」页 → 点确认就成，手机不用动。")
        print("      若只弹出「前往 / 复制链接」，或提示「请用手机微信扫码」→ 这个码不允许")
        print("      从相册识别，PC 微信这条路走不通（腾讯自家部分站点属此类），只能手机扫；")
        print("      这时若站点支持「QQ 登录 / 账号密码登录」，换那个反而最省事。")
    else:
        print("\n    → 内容已存盘；网址类可以直接在电脑浏览器打开。")
    print("")
    return True


def grab_screen() -> Image.Image:
    try:
        return ImageGrab.grab(all_screens=True)
    except TypeError:
        return ImageGrab.grab()


# ------------------------------ 自测：负例 -> 正例 ------------------------------
def selftest():
    print("=" * 66)
    print("桌面扫码兜底 · 自测（负例 -> 正例 -> 难例 -> 真实登录码闭环 四轮）")
    print("=" * 66)

    # 第 1 轮：空白图，预期识别失败
    blank = Image.new("RGB", (420, 300), (245, 246, 248))
    r1, _ = decode_pil(blank)
    print(f"[轮次 1 / 负例] 空白图 -> 识别结果 {len(r1)} 个；预期 0 个 -> "
          f"{'PASS' if len(r1) == 0 else 'FAIL'}")

    # 第 2 轮：真实二维码，预期识别出原文本
    payload = "https://open.weixin.qq.com/connect/qrconnect?appid=wxSELFTEST&scope=snsapi_login"
    enc = cv2.QRCodeEncoder_create()
    qr_bgr = enc.encode(payload)
    if qr_bgr is None or getattr(qr_bgr, "size", 0) == 0:
        print("[轮次 2 / 正例] SKIP：本机 OpenCV 不支持 QRCodeEncoder")
        return 1
    if qr_bgr.ndim == 2:
        qr_rgb = cv2.cvtColor(qr_bgr, cv2.COLOR_GRAY2RGB)
    else:
        qr_rgb = cv2.cvtColor(qr_bgr, cv2.COLOR_BGR2RGB)
    # 放大 + 加白边，模拟屏幕截图里的登录码
    big = cv2.resize(qr_rgb, None, fx=5, fy=5, interpolation=cv2.INTER_NEAREST)
    big = cv2.copyMakeBorder(big, 40, 40, 40, 40, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    test_img = Image.fromarray(big)
    test_path = os.path.join(OUT, "selftest_qr.png")
    test_img.save(test_path)

    r2, cropped = decode_pil(test_img)
    hit = bool(r2) and r2[0] == payload
    print(f"[轮次 2 / 正例] 生成二维码 -> 识别 {len(r2)} 个；内容一致: {hit} -> "
          f"{'PASS' if hit else 'FAIL'}")
    print(f"    生成: {test_path}")
    print(f"    解码: {r2[0][:120] if r2 else '(空)'}")
    if cropped:
        print(f"    自动裁切区域: {cropped.size[0]}x{cropped.size[1]} px")

    # 第 3 轮：退化图像 —— 修复前单引擎会挂、修复后引擎链应能过
    hard = Image.fromarray(255 - cv2.GaussianBlur(big, (5, 5), 0))  # 反色 + 模糊
    legacy_hits = decode_legacy(hard)
    new_hits, _ = decode_pil(hard)
    legacy_ok = any(payload in s for s in legacy_hits)
    new_ok = any(payload in s for s in new_hits)
    print(f"[轮次 3 / 难例] 反色+高斯模糊 -> 修复前单引擎: {'OK' if legacy_ok else 'FAIL'}"
          f" / 修复后引擎链: {'OK' if new_ok else 'FAIL'} -> {'PASS' if new_ok else 'FAIL'}")

    # 第 4 轮：真实场景闭环（2026-11-09 桌面客户端 Marvis 登录弹窗截图，码内容为 connect/confirm?uuid=）
    # 验证「识别 -> 裁切 -> 按微信识码建议放大 -> 二次解码」内容不变，且分类命中登录码。
    confirm = "https://open.weixin.qq.com/connect/confirm?uuid=001kaLzs0b2Tml2o"
    c_bgr = enc.encode(confirm)
    c_rgb = cv2.cvtColor(c_bgr, cv2.COLOR_GRAY2RGB) if c_bgr.ndim == 2 else cv2.cvtColor(c_bgr, cv2.COLOR_BGR2RGB)
    c_rgb = cv2.resize(c_rgb, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST)
    c_rgb = cv2.copyMakeBorder(c_rgb, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=(255, 255, 255))
    c_img = Image.fromarray(c_rgb)
    c_hits, c_crop = decode_pil(c_img)
    c_up, did_up, c_scale = prepare_for_wechat(c_crop or c_img)
    c_back, _ = decode_pil(c_up)
    cls_ok = classify(confirm) == "微信登录/授权二维码"
    loop_ok = bool(c_hits) and c_hits[0] == confirm and any(s == confirm for s in c_back)
    print(f"[轮次 4 / 真实登录码闭环] connect/confirm?uuid 分类: {'OK' if cls_ok else 'FAIL'}"
          f" / 裁切 {(c_crop or c_img).size} -> 放大 {c_up.size} (x{c_scale})"
          f" / 二次解码: {'OK' if loop_ok else 'FAIL'} -> {'PASS' if (cls_ok and loop_ok) else 'FAIL'}")

    ok = (len(r1) == 0) and hit and new_ok and cls_ok and loop_ok
    print(f"\n结论: {'四轮全部通过' if ok else '存在失败项'}  "
          f"(负例={len(r1)==0}, 正例={hit}, 难例={new_ok}, 登录码闭环={cls_ok and loop_ok})")
    return 0 if ok else 1


def bench():
    """修复前 / 修复后：10 种退化图像上的识别率对比（可核查的数据 diff）"""
    payload = "https://open.weixin.qq.com/connect/qrconnect?appid=wxBENCH&scope=snsapi_login&state=xyz"
    enc = cv2.QRCodeEncoder_create()
    base = enc.encode(payload)
    base = cv2.copyMakeBorder(base, 30, 30, 30, 30, cv2.BORDER_CONSTANT, value=255)
    base = cv2.resize(base, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
    if base.ndim == 3:
        base = cv2.cvtColor(base, cv2.COLOR_BGR2GRAY)

    def rot(im, deg):
        h, w = im.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
        return cv2.warpAffine(im, m, (w, h), borderValue=255)

    noise = base.copy()
    rng = np.random.default_rng(7)
    mask = rng.random(noise.shape) < 0.02
    noise[mask] = rng.integers(0, 256, mask.sum(), dtype=np.uint8)
    cases = [
        ("clean", base.copy()),
        ("gaussian_blur3", cv2.GaussianBlur(base, (3, 3), 0)),
        ("gaussian_blur5", cv2.GaussianBlur(base, (5, 5), 0)),
        ("rot_15deg", rot(base, 15)),
        ("rot_30deg", rot(base, 30)),
        ("inverted", 255 - base),
        ("low_contrast", np.clip((base.astype(np.float32) - 128) * 0.45 + 128, 0, 255).astype(np.uint8)),
        ("small_0.4x", cv2.resize(base, None, fx=0.4, fy=0.4, interpolation=cv2.INTER_AREA)),
        ("salt_noise", noise),
        ("jpeg_q20", cv2.imdecode(cv2.imencode('.jpg', base, [cv2.IMWRITE_JPEG_QUALITY, 20])[1],
                                 cv2.IMREAD_GRAYSCALE)),
    ]
    print("=" * 66)
    print("识别引擎回归对比：修复前 QRCodeDetector  ->  修复后 WeChatCV 引擎链")
    print("=" * 66)
    print(f"{'case':<18}{'修复前':<14}{'修复后':<14}")
    print("-" * 46)
    a = b = 0
    for name, arr in cases:
        im = Image.fromarray(arr)
        r1 = any(payload in s for s in decode_legacy(im))
        r2 = any(payload in s for s in decode_pil(im)[0])
        a += r1; b += r2
        print(f"{name:<18}{('OK' if r1 else 'FAIL'):<14}{('OK' if r2 else 'FAIL'):<14}")
    print("-" * 46)
    print(f"{'TOTAL':<18}{f'{a}/{len(cases)}':<14}{f'{b}/{len(cases)}':<14}")
    print(f"\n结论: 修复前 {a}/{len(cases)}，修复后 {b}/{len(cases)}，"
          f"净增 {b - a} 个可识别场景")
    return 0 if b >= a else 1


# ------------------------------ 全局热键 ------------------------------
def hotkey_loop():
    if sys.platform != "win32":
        print("全局热键仅支持 Windows；请用 --once 或 --image。")
        return 1
    u32 = ctypes.windll.user32
    MOD_ALT, MOD_NOREPEAT, WM_HOTKEY, VK_Q = 0x0001, 0x4000, 0x0312, 0x51

    class MSG(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                    ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_size_t),
                    ("time", ctypes.c_uint), ("pt_x", ctypes.c_long), ("pt_y", ctypes.c_long)]

    if not u32.RegisterHotKey(None, 1, MOD_ALT | MOD_NOREPEAT, VK_Q):
        print("Alt+Q 注册失败（可能被别的程序占用）。可改用 --once。")
        return 1
    print("桌面扫码兜底已启动 ✅  按 Alt+Q 截屏识别二维码；Ctrl+C 退出。")
    print("（这个黑窗口要一直开着别关，回到登录窗口按 Alt+Q）")
    print("（识别结果会自动存到 out/，并把二维码图片放进剪贴板）\n")
    msg = MSG()
    try:
        while True:
            ret = u32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (-1, 0):
                break
            if msg.message == WM_HOTKEY and msg.wParam == 1:
                time.sleep(0.25)  # 等热键本身引起的界面变化稳定
                handle_shot(grab_screen(), "Alt+Q 截屏")
            u32.TranslateMessage(ctypes.byref(msg))
            u32.DispatchMessageW(ctypes.byref(msg))
    except KeyboardInterrupt:
        print("\n已退出。")
    finally:
        u32.UnregisterHotKey(None, 1)
    return 0


def main():
    ap = argparse.ArgumentParser(description="桌面二维码兜底识别（PC 微信内完成确认）")
    ap.add_argument("--selftest", action="store_true", help="负例+正例+难例三轮自测")
    ap.add_argument("--bench", action="store_true", help="修复前/后 10 种退化图像识别率对比")
    ap.add_argument("--once", action="store_true", help="立即截屏识别一次")
    ap.add_argument("--image", help="识别指定图片文件")
    args = ap.parse_args()

    if args.bench:
        return bench()
    if args.selftest:
        return selftest()
    if args.image:
        if not os.path.exists(args.image):
            print("文件不存在:", args.image); return 2
        return 0 if handle_shot(Image.open(args.image), os.path.basename(args.image)) else 1
    if args.once:
        return 0 if handle_shot(grab_screen(), "单次截屏") else 1
    return hotkey_loop()


if __name__ == "__main__":
    sys.exit(main())
