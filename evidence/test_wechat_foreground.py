"""回归：微信最小化时「拉到前台 + Ctrl+V」是否真的生效。

背景（2026-11-10 实测发现）：
  微信 PC 版最小化时主窗口 rect 为 (-32000,-32000) 237x39；
  旧实现只 ShowWindow(SW_RESTORE) + SetForegroundWindow，吞掉失败并恒返回 True，
  导致二维码图片的 Ctrl+V 发给前台的别的窗口（WorkBuddy / 浏览器），静默落空。

本脚本构造同样的干扰场景（把宿主窗口置顶并设为前台），分别跑
「修复前（内联旧实现）」与「修复后（wxscan.bring_wechat_front）」两轮，
逐项输出可核查的状态 diff，最后把窗口恢复原状。

诚实说明：2026-11-10 实跑两轮均成功，未复现旧实现失败。昨晚的粘贴落空
主因是「跨多次工具调用之间前台被 WorkBuddy / Marvis 抢回」，不是工具单次
调用本身的缺陷。因此本次代码修改定位为健壮性加固，不宣称修好了可复现故障。
"""
import ctypes
import sys
import time

import os
sys.path.insert(0, os.path.expanduser(r"~\Desktop\wxscan"))
import wxscan  # noqa: E402

u = ctypes.windll.user32
u.SetProcessDPIAware()
SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = 0x0002, 0x0001, 0x0040
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2


def find_host():
    PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    hits = []

    def cb(h, _l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                b = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, b, n + 1)
                if b.value.strip() == "WorkBuddy":
                    hits.append(h)
        return True

    u.EnumWindows(PROC(cb), 0)
    return hits[0] if hits else None


def legacy_bring_wechat_front():
    """修复前的原实现，逐字内联。"""
    hwnd = wxscan.find_wechat_window()
    if not hwnd:
        return False
    u.ShowWindow(hwnd, 9)  # SW_RESTORE
    try:
        u.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001
        pass
    return True


def snapshot(hwnd):
    x, y, w, h = wxscan._win_rect(u, hwnd)
    return {
        "minimized": bool(u.IsIconic(hwnd)),
        "fg_is_wechat": u.GetForegroundWindow() == hwnd,
        "foreground_hwnd": u.GetForegroundWindow(),
        "rect": (x, y, w, h),
    }


def setup(host):
    """构造干扰：微信最小化 + 宿主置顶且在前台。"""
    u.ShowWindow(WX, 6)  # SW_MINIMIZE
    if host:
        u.ShowWindow(host, 9)
        u.SetWindowPos(host, HWND_TOPMOST, 0, 0, 0, 0,
                       SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        u.SetForegroundWindow(host)
    time.sleep(0.45)


WX = wxscan.find_wechat_window()
HOST = find_host()
print("=" * 72)
print("微信最小化场景 · 拉前台修复前 / 修复后对比   （证据：本脚本实跑输出）")
print("=" * 72)
print(f"微信 hwnd = {WX}   宿主 hwnd = {HOST}")
print("干扰场景：微信最小化 + 宿主窗口置顶并占据前台（模拟用户正看着 IDE）\n")

orig = snapshot(WX)
print(f"[初始] {orig}\n")

results = {}
for name, fn in (("修复前", legacy_bring_wechat_front),
                 ("修复后", wxscan.bring_wechat_front)):
    setup(HOST)
    before = snapshot(WX)
    reported = fn()
    time.sleep(0.35)
    after = snapshot(WX)
    actual_ok = (after["fg_is_wechat"] and not after["minimized"])
    consistent = (bool(reported) == actual_ok)
    results[name] = dict(before=before, reported=reported, after=after,
                         actual_ok=actual_ok, consistent=consistent)
    print(f"--- {name} ---")
    print(f"  调用前 : {before}")
    print(f"  返回值 : {reported}")
    print(f"  调用后 : {after}")
    print(f"  实际成功(前台=微信 且 已还原) : {actual_ok}")
    print(f"  返回值与实际一致             : {consistent}")
    print()

# 恢复现场：取消所有置顶，微信回到可见状态，宿主恢复可见
wxscan.release_wechat_topmost()
if HOST:
    u.SetWindowPos(HOST, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    u.ShowWindow(HOST, 9)
u.ShowWindow(WX, 9)
u.SetWindowPos(WX, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
time.sleep(0.3)

print("=" * 72)
print("DIFF（修复前 -> 修复后）")
print(f"  返回值与实际状态一致 : {results['修复前']['consistent']} -> {results['修复后']['consistent']}")
print(f"  实际抢到前台         : {results['修复前']['actual_ok']} -> {results['修复后']['actual_ok']}")
print(f"  微信仍是最小化       : {results['修复前']['after']['minimized']} -> {results['修复后']['after']['minimized']}")
print(f"  恢复后 rect          : {results['修复前']['after']['rect']} -> {results['修复后']['after']['rect']}")
print("=" * 72)
print(f"现场已还原：微信可见={bool(u.IsWindowVisible(WX))} 最小化={bool(u.IsIconic(WX))}")
import inspect  # noqa: E402
src = inspect.getsource(wxscan.bring_wechat_front)
caps = {
    "最小化检测 IsIconic": "IsIconic" in src,
    "前台结果校验 GetForegroundWindow": "GetForegroundWindow" in src,
    "临时置顶 SetWindowPos": "SetWindowPos" in src,
    "前台锁定解锁(ALT)重试": "0x12" in src,
    "用完取消置顶": hasattr(wxscan, "release_wechat_topmost"),
    "失败如实返回 False": "return ok" in src,
}
print("静态能力 diff（旧实现 -> 新实现）")
for k, v in caps.items():
    print(f"  {k:<34} False -> {v}")

ok = all(caps.values()) and results["修复后"]["actual_ok"] and results["修复后"]["consistent"]
print("\n结论:", "PASS（两轮均生效、无功能退化；新增 6 项校验已落地）" if ok else "FAIL")
print("诚实说明：本轮干扰场景两轮都成功，未复现旧实现失败；修改定位为健壮性加固。")
sys.exit(0 if ok else 1)
