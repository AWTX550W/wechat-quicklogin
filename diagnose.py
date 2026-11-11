#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
微信电脑确认 · 本机通道诊断（独立可提取，纯标准库，绿色免安装）

只做只读探测，不改注册表、不改浏览器配置、不碰微信客户端与账号数据。
不依赖 PowerShell / wmic / 第三方包：进程与文件版本全部走 Win32 API (ctypes)。

用法:
    python diagnose.py                 # 人类可读报告
    python diagnose.py --json          # 机器可读
    python diagnose.py --save          # 同时把报告写入 evidence/diagnose_<时间>.txt
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import socket
import ssl
import subprocess
import sys
from ctypes import wintypes
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
WX_LOCAL_HOST = "localhost.weixin.qq.com"
KNOWN_PORTS = [13013, 13015, 14013, 14016, 14019, 14022, 14023, 14026]
MIN_WX_WINDOWS = (3, 9, 11)
IS_WIN = sys.platform == "win32"


def _ver_tuple(s):
    nums = [int(x) for x in re.findall(r"\d+", s or "")[:4]]
    return tuple(nums + [0] * (4 - len(nums)))


# ---------------------------------------------------------------------------
# Win32 探测层：进程 PID -> 完整路径 -> 文件版本。全程 ctypes，无外部命令依赖。
# ---------------------------------------------------------------------------
class VS_FIXEDFILEINFO(ctypes.Structure):
    _fields_ = [
        ("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
        ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD),
        ("dwProductVersionMS", wintypes.DWORD), ("dwProductVersionLS", wintypes.DWORD),
        ("dwFileVersionFlagsMask", wintypes.DWORD), ("dwFileVersionFlags", wintypes.DWORD),
        ("dwFileVersionOS", wintypes.DWORD), ("dwFileVersionType", wintypes.DWORD),
        ("dwFileVersionSubtype", wintypes.DWORD), ("dwFileVersionDateMS", wintypes.DWORD),
        ("dwFileVersionDateLS", wintypes.DWORD),
    ]


class _Win32:
    def __init__(self):
        self.ok = False
        if not IS_WIN:
            return
        try:
            self.psapi = ctypes.WinDLL("psapi")
            self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self.ver = ctypes.WinDLL("version")
            self.psapi.EnumProcesses.argtypes = [
                ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
            self.k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.k32.OpenProcess.restype = wintypes.HANDLE
            self.k32.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            self.k32.CloseHandle.argtypes = [wintypes.HANDLE]
            self.ver.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
            self.ver.GetFileVersionInfoSizeW.restype = wintypes.DWORD
            self.ver.GetFileVersionInfoW.argtypes = [
                wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
            self.ver.VerQueryValueW.argtypes = [
                ctypes.c_void_p, wintypes.LPCWSTR,
                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
            self.ok = True
        except Exception:  # noqa: BLE001
            self.ok = False

    def processes(self):
        """-> [(pid, full_path)]，失败返回 []"""
        if not self.ok:
            return []
        arr = (wintypes.DWORD * 8192)()
        needed = wintypes.DWORD()
        if not self.psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            return []
        pids = list(arr[: needed.value // ctypes.sizeof(wintypes.DWORD)])
        out = []
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        for pid in pids:
            h = self.k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                continue
            try:
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(32768)
                if self.k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    out.append((pid, buf.value))
            finally:
                self.k32.CloseHandle(h)
        return out

    def file_version(self, path):
        """读 PE 资源里的 VS_FIXEDFILEINFO；不依赖语言代码页"""
        if not self.ok or not path or not os.path.exists(path):
            return None
        try:
            size = self.ver.GetFileVersionInfoSizeW(path, None)
            if not size:
                return None
            buf = ctypes.create_string_buffer(size)
            if not self.ver.GetFileVersionInfoW(path, 0, size, buf):
                return None
            ptr = ctypes.c_void_p()
            length = wintypes.UINT()
            if not self.ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
                return None
            fi = ctypes.cast(ptr, ctypes.POINTER(VS_FIXEDFILEINFO)).contents
            ms, ls = fi.dwFileVersionMS, fi.dwFileVersionLS
            return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
        except Exception:  # noqa: BLE001
            return None


WIN = _Win32()


def _run(cmd, timeout=12):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        out = p.stdout or b""
        for enc in ("gbk", "utf-8", "latin-1"):
            try:
                return out.decode(enc)
            except UnicodeDecodeError:
                continue
        return out.decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return f"__ERROR__ {type(e).__name__}: {e}"


def probe_wechat():
    res = {"installed": None, "running": None, "version": None, "path": None,
           "listening_ports": [], "meets_version": None,
           "probe_ok": WIN.ok, "netstat_ok": None, "process_count": 0}
    procs = WIN.processes()
    res["process_count"] = len(procs)
    if WIN.ok:
        wx_procs = [(pid, path) for pid, path in procs
                    if re.search(r"(weixin|wechat)\.exe$", os.path.basename(path), re.I)]
        main = next(((pid, path) for pid, path in wx_procs
                     if os.path.basename(path).lower() == "weixin.exe"),
                    wx_procs[0] if wx_procs else None)
        res["running"] = bool(main)
        res["installed"] = bool(main)
        if main:
            res["path"] = main[1]
            res["version"] = WIN.file_version(main[1])
            if res["version"]:
                res["meets_version"] = _ver_tuple(res["version"])[:3] >= MIN_WX_WINDOWS

        # netstat -ano 拿监听端口，用上面的 PID 集合过滤
        ns = _run(["netstat", "-ano", "-p", "TCP"])
        res["netstat_ok"] = not ns.startswith("__ERROR__")
        wx_pids = {pid for pid, _ in wx_procs}
        if res["netstat_ok"]:
            for line in ns.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                    try:
                        pid, port = int(parts[4]), int(parts[1].rsplit(":", 1)[-1])
                    except ValueError:
                        continue
                    if pid in wx_pids:
                        res["listening_ports"].append({"address": parts[1], "port": port, "pid": pid})
            seen = set()
            res["listening_ports"] = [x for x in res["listening_ports"]
                                      if not (x["port"] in seen or seen.add(x["port"]))]
            res["listening_ports"].sort(key=lambda x: x["port"])
    return res


def probe_dns():
    try:
        return {"ok": True, "host": WX_LOCAL_HOST,
                "addresses": socket.gethostbyname_ex(WX_LOCAL_HOST)[2]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def probe_browsers():
    out = []
    cands = [
        ("Microsoft Edge", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ("Microsoft Edge", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ("Google Chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ("Google Chrome", r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
    ]
    for name, path in cands:
        if not os.path.exists(path):
            continue
        ver = WIN.file_version(path)
        if not ver:
            continue
        major = _ver_tuple(ver)[0]
        out.append({"name": name, "version": ver, "major": major,
                    "lna_risk": major >= 142, "path": path})
    seen = set(); uniq = []
    for b in out:
        if (b["name"], b["version"]) not in seen:
            seen.add((b["name"], b["version"])); uniq.append(b)
    return uniq


def probe_local_service(ports=None, timeout=1.2):
    """TCP + TLS 直连探测。结果如实记录，不把失败解释成「通道不可用」。"""
    ports = ports or KNOWN_PORTS
    rows = []
    for port in ports:
        row = {"port": port, "tcp": False, "tls": None, "http": None}
        s = socket.socket(); s.settimeout(timeout)
        try:
            s.connect(("127.0.0.1", port)); row["tcp"] = True
        except Exception as e:  # noqa: BLE001
            row["tcp_error"] = type(e).__name__
        finally:
            s.close()
        if row["tcp"]:
            try:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sk:
                    with ctx.wrap_socket(sk, server_hostname=WX_LOCAL_HOST) as ss:
                        row["tls"] = "OK"
                        ss.sendall(
                            b"GET /api/check-login HTTP/1.1\r\n"
                            b"Host: localhost.weixin.qq.com\r\n"
                            b"Origin: https://open.weixin.qq.com\r\n"
                            b"Connection: close\r\n\r\n"
                        )
                        row["http"] = repr(ss.recv(200)[:200])
            except Exception as e:  # noqa: BLE001
                row["tls"] = f"{type(e).__name__}: {e}"[:160]
        rows.append(row)
    return rows


def build_report():
    wx = probe_wechat()
    dns = probe_dns()
    browsers = probe_browsers()
    ports = sorted({p["port"] for p in wx["listening_ports"]} | set(KNOWN_PORTS))
    svc = probe_local_service(ports)

    findings = []
    # 探测能力自检：工具失败绝不写成事实结论
    if not wx["probe_ok"]:
        findings.append(("warn", "Win32 进程探测不可用，以下关于微信客户端/浏览器的结论均标为「未知」，不能作为事实判断"))
    if wx["probe_ok"] and wx["netstat_ok"] is False:
        findings.append(("warn", "netstat 不可用，监听端口列表可能不完整"))

    if wx["running"] is None:
        findings.append(("warn", "无法判定 PC 微信客户端是否运行（探测器不可用）"))
    elif not wx["running"]:
        findings.append(("bad", "未检测到运行中的 PC 微信客户端；快速登录必须有已登录且未锁定的客户端"))
    elif wx["meets_version"] is False:
        findings.append(("bad", f"微信版本 {wx['version']} 低于 3.9.11，不支持网站应用快速登录"))
    elif wx["meets_version"] is None:
        findings.append(("warn", f"检测到 PC 微信 {wx['path']}，但读不到版本号，无法判定是否满足 ≥3.9.11"))
    else:
        findings.append(("ok", f"PC 微信 {wx['version']} 正在运行，满足 ≥3.9.11 的版本要求"))

    if wx["listening_ports"]:
        findings.append(("ok", "本地服务正在监听: " + ", ".join(
            f"127.0.0.1:{p['port']}" for p in wx["listening_ports"])))
        if any(p["port"] == 14013 for p in wx["listening_ports"]):
            findings.append(("ok", "14013 在监听 —— 正是微信登录 iframe 探测的 check-login 端口"))
    else:
        findings.append(("warn" if wx["netstat_ok"] is False else "bad",
                         "未取得微信客户端的本地监听端口（或确实没有）"))

    if dns.get("ok"):
        findings.append(("ok", f"DNS: {WX_LOCAL_HOST} -> {', '.join(dns['addresses'])}"))
    else:
        findings.append(("bad", "DNS 解析失败: " + dns.get("error", "")))

    risky = [b for b in browsers if b["lna_risk"]]
    if risky:
        findings.append(("warn", "命中 LNA 限制的浏览器: " + ", ".join(
            f"{b['name']} {b['version']}" for b in risky) +
            " —— Chrome 142+/Edge 143+ 会拦掉登录 iframe 对 127.0.0.1 的探测，静默回退扫码"))
    elif browsers:
        findings.append(("ok", "已发现的浏览器均未命中 ≥142 的 LNA 限制"))
    else:
        findings.append(("warn", "未读到浏览器版本（探测器不可用或未安装 Chrome/Edge），LNA 风险未知"))

    tls_fail = sum(1 for r in svc if r["tcp"] and r["tls"] and r["tls"] != "OK")
    if tls_fail:
        findings.append(("warn", f"{tls_fail} 个本地端口 TCP 可连但 TLS 握手被对端断开。" +
                         "微信本地服务使用内部 TLS/客户端指纹校验，非浏览器直连被拒属预期，" +
                         "不能据此判定快速登录通道不可用；判据是「登录页是否出现快捷登录头像」"))

    return {
        "time": datetime.now().isoformat(timespec="seconds"),
        "wechat": wx, "dns": dns, "browsers": browsers,
        "local_service": svc, "findings": findings,
    }


def human(r):
    L = []
    L.append("=" * 68)
    L.append("微信电脑确认 · 本机通道诊断  " + r["time"])
    L.append("=" * 68)
    wx = r["wechat"]
    L.append("\n[1] PC 微信客户端")
    L.append(f"    探测器    : Win32 API={'OK' if wx['probe_ok'] else 'FAIL'}  "
             f"进程数={wx['process_count']}  netstat={'OK' if wx['netstat_ok'] else 'FAIL/未知'}")
    L.append(f"    路径      : {wx.get('path') or '未找到/未知'}")
    L.append(f"    版本      : {wx.get('version') or '未知'}  (要求 Windows >= 3.9.11)")
    L.append(f"    监听端口  : {', '.join('127.0.0.1:' + str(p['port']) for p in wx['listening_ports']) or '无/未知'}")
    L.append("\n[2] 浏览器")
    for b in r["browsers"]:
        L.append(f"    {b['name']:16s} {b['version']:18s} LNA 风险: {'是' if b['lna_risk'] else '否'}")
    if not r["browsers"]:
        L.append("    (未读到 Chrome/Edge 版本)")
    L.append("\n[3] DNS / 本地服务直连")
    d = r["dns"]
    L.append(f"    {WX_LOCAL_HOST} -> {', '.join(d.get('addresses', [])) if d.get('ok') else d.get('error')}")
    for row in r["local_service"]:
        L.append(f"    :{row['port']:<6} TCP={'OK' if row['tcp'] else 'FAIL':<4} "
                 f"TLS={row['tls'] or '-'}")
    L.append("\n[4] 结论")
    for level, text in r["findings"]:
        L.append(f"    [{level.upper():4s}] {text}")
    L.append("\n[5] 修复清单（按优先级）")
    steps = [
        "在需要登录的网站上：地址栏左侧锁图标 → 网站设置 → 「本地网络访问」改为「允许」。"
        "之前点过「阻止」必须改回来；Chrome 142+ 首次弹窗要点「允许」。",
        "安装同目录 extension/ 扩展（Edge/Chrome 开发者模式加载已解压的扩展），"
        "它会自动给微信登录 iframe 补 allow=\"local-network-access ...\"，并给 wxLogin.js 加 no-cache。",
        "刷新登录页，点状态条的「重新检测」。出现头像 +「快捷登录」即修复成功，电脑上点确认即可。",
        "确认 PC 微信处于已登录且未锁定状态；网站自己传 fast_login=0 的会强制扫码，扩展不改业务参数。",
        "老站点没接快速登录的，用 desktop-scan/ 的热键工具截屏识别二维码，送进 PC 微信「识别图中二维码」。",
    ]
    for i, s in enumerate(steps, 1):
        L.append(f"    {i}. {s}")
    L.append("\n[6] 边界（未做 / 不声称）")
    L.append("    - 本工具不绕过微信的安全模型，不模拟手机、不伪造授权、不碰账号与 Cookie。")
    L.append("    - 快速登录是微信官方能力；能不能用取决于网站是否接入、客户端状态、浏览器权限。")
    L.append("    - TLS 直连失败是微信内部协议所致，本诊断不把它当作「通道不可用」的证据。")
    L.append("    - 未做：真实的第三方网站登录端到端实测（需要用户实际打开一个登录页）。")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="微信快速登录通道本机诊断")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--save", action="store_true", help="保存报告到 evidence/")
    args = ap.parse_args()

    r = build_report()
    text = json.dumps(r, ensure_ascii=False, indent=2) if args.json else human(r)
    print(text)

    if args.save:
        ev = os.path.join(ROOT, "evidence")
        os.makedirs(ev, exist_ok=True)
        fn = os.path.join(ev, "diagnose_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt")
        with open(fn, "w", encoding="utf-8") as f:
            f.write(human(r))
        print("\n[已保存] " + fn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
